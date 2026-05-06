# Runbook — Deploy the Cloud-AWS Hybrid Site

## What this builds

A site-to-site IPsec tunnel from `dc-ce-1` (Cisco C8000v in EVE-NG) to a
self-managed strongSwan EC2 in AWS, plus a scheduled Lambda that watches
the tunnel from the AWS side. End state:

- **AWS side** — `t3.micro` strongSwan EC2 (Ubuntu 22.04 LTS, IMDSv2-only)
  with EIP, Secrets Manager-backed PSK, custom SSM document with the
  `swanctl --list-sas` command hardcoded, IAM-scoped Lambda monitor with
  region+account conditions, two CloudWatch alarms (tunnel-down vs.
  lambda-broken via metric-math), SNS email alerts.
- **Lab side** — a `cloud_aws` site in NetBox, an IPsec block on `dc-ce-1`
  rendered from the spec, an MCP tool (`cloud_tunnel_health`) that reports
  IKEv2/IPsec state.
- **Pipeline** — push-time PSK injection (`scripts/push_configs.py`) pulls
  the secret from AWS Secrets Manager at deploy time so the rendered
  config in `configs/generated/dc-ce-1.cfg` stays commit-safe.
- **State backend** — S3 with customer-managed KMS CMK encryption, TLS-only
  bucket policy, versioned, public access blocked. DynamoDB lock table
  with PITR enabled.

## Prerequisites

- AWS account with the free tier active (or budget acceptance). The vpn
  module fits inside the 12-month free tier; the existing
  `cloud-devops-pipeline` `dev` env runs a NAT Gateway (~$32/mo) and ALB
  (~$16/mo), so destroy when not in use.
- `cloud-devops-pipeline` repo cloned, referred to here as `$AWS_REPO`.
- Terraform ≥ 1.5, AWS CLI v2, IAM creds with permission to create EC2,
  EIP, Secrets Manager, IAM, Lambda, CloudWatch, SNS, EventBridge, KMS,
  SSM (including custom Documents), S3, DynamoDB.
- The lab is already populated: `make generate-spec` succeeds against the
  21-node baseline, `dc-ce-1` is reachable via SSH from this host,
  NetBox is up.
- A reachable public IP for IKE ingress (your home/office uplink, or the
  Proxmox host's public IP). The strongSwan SG only allows UDP 500/4500/ESP
  from this `/32`. **Validated regex** — only literal IPv4 dotted quads
  accepted, no expressions or spaces.

## Cost expectations

| Resource | Monthly cost |
|---|---|
| `t3.micro` strongSwan (12-month free tier) | $0 |
| EIP (attached) | $0 (charged $3.60/mo only when **detached**) |
| Lambda @ rate(5 min) ≈ 8.6k invocations | $0 (1M invocations free) |
| CloudWatch — 2 alarms + custom metrics | $0 (10 metrics + 10 alarms free) |
| SNS email | $0 (1k notifications free) |
| Secrets Manager | $0.40 + ~$0 API calls |
| KMS CMK | $1.00 (always charged) |
| Existing `cloud-devops-pipeline` (NAT GW + ALB) | ~$48 |

Set a $1/mo Budget alarm on first apply (one-time):

```bash
aws budgets create-budget \
  --account-id $(aws sts get-caller-identity --query Account --output text) \
  --budget '{"BudgetName":"vpn-lab-1usd","BudgetLimit":{"Amount":"1","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOU@example.com"}]}]'
```

## One-time setup

```bash
# 1. Lab side: confirm .env exposes the AWS-coupling vars (see .env.example)
grep -E '^(AWS_REPO|ONPREM_PUBLIC_IP)=' .env || echo "fill these in"

# 2. Export the AWS repo path so `make sync-aws-outputs` can find it
export AWS_REPO=/Users/<you>/PycharmProjects/cloud-devops-pipeline

# 3. AWS side: confirm CLI auth + region
aws sts get-caller-identity
aws configure get region   # should print us-east-1
```

## Build sequence

The full deploy is **eight commands**, ~6–8 min total.

### 1. Bootstrap the state backend (one-time per account)

The S3 bucket and DynamoDB lock table that hold Terraform's state can't
manage themselves with Terraform — chicken-and-egg. The bootstrap script
captures that out-of-band step as code:

```bash
bash $AWS_REPO/scripts/bootstrap-state-backend.sh
```

What it does (idempotent — safe to re-run):
1. Creates a customer-managed KMS CMK with annual rotation
   (`alias/cloud-devops-pipeline-tfstate-key`)
2. Applies SSE-KMS encryption to the state bucket using that CMK
3. Applies a bucket policy denying non-TLS access AND non-KMS uploads
4. Re-asserts versioning + public-access-block
5. Enables Point-In-Time Recovery on the DynamoDB lock table

**Why this matters**: every `random_password` in the modules — including
the IPsec PSK — lands in `terraform.tfstate` in cleartext. State at rest
MUST be CMK-encrypted, not just SSE-S3, so we control the key policy,
rotation, and CloudTrail audits per-key.

### 2. Apply AWS side

```bash
cd $AWS_REPO/terraform/environments/dev
terraform init   # picks up the kms_key_id in the backend block
terraform apply \
  -var enable_vpn=true \
  -var onprem_public_ip=<your.public.ip> \
  -var vpn_alert_email=<you@example.com>
```

**If the secret is in 7-day recovery**: a previous destroy schedules
`vpn/onprem-psk` for deletion. To re-apply within 7 days, force-delete it:

```bash
aws secretsmanager delete-secret --secret-id vpn/onprem-psk --force-delete-without-recovery
```

Apply finishes when `null_resource.strongswan_ready` clears (~75 sec on
Ubuntu — the gate polls SSM until `systemctl is-active strongswan-starter`
returns `active`). You'll get an SNS subscription confirmation email —
click it. Without confirmation alerts fire silently.

### 3. Pull AWS outputs into the lab

```bash
cd $LAB_REPO        # back to Eve-NG_Agent
make sync-aws-outputs
```

Writes `.aws_outputs.json` (gitignored — contains the strongSwan EIP and
PSK secret ARN, not the secret itself).

```bash
export AWS_STRONGSWAN_EIP=$(jq -r .strongswan_eip.value < .aws_outputs.json)
export AWS_VPN_PSK_SECRET_ARN=$(jq -r .psk_secret_arn.value < .aws_outputs.json)
```

### 4. Seed NetBox with the cloud-aws site

```bash
make seed-cloud-aws
```

Idempotent — safe to re-run after an `apply` that changed any of the
outputs.

### 5. Regenerate spec + configs

```bash
make generate-spec
make generate-configs
```

Sanity-check that the rendered config carries only the marker (no
plaintext PSK):

```bash
grep -c '__AWS_VPN_PSK__' configs/generated/dc-ce-1.cfg   # expect: 1
grep -c 'pre-shared-key '  configs/generated/dc-ce-1.cfg   # expect: 1
```

### 6. Validate

```bash
make validate-batfish
```

**Known coverage gap**: Batfish does not model Cisco IKEv2/IPsec crypto
blocks, so a green `validate-batfish` does **not** confirm the IPsec
tunnel will come up. Ground truth comes from `cloud_tunnel_health` in
step 7.

### 7. Push to dc-ce-1

```bash
export API_AUTH_TOKEN=<the-bearer-token-for-the-API>   # only if pushing through API
make push-configs DEVICE=dc-ce-1
```

`scripts/push_configs._inject_aws_psk` reads `AWS_VPN_PSK_SECRET_ARN`,
fetches the secret from AWS at this exact moment, substitutes the marker
in memory, sends to the device. The plaintext PSK never lands on disk.

### 8. Cross-confirm the tunnel state

Wait ~30 sec for IKE to negotiate, then:

```bash
ssh dc-ce-1 'show crypto ikev2 sa | include READY'
```

## Verify (four independent signals)

The plan calls for cross-verification because each method can lie in a
different way. All four should agree.

### A. On the device

```bash
ssh dc-ce-1 'show crypto ikev2 sa detail'
ssh dc-ce-1 'show crypto ipsec sa | include encrypt|decrypt'
```

### B. Via the MCP tool (agent's view)

From Claude Code or the FastMCP client:

```
cloud_tunnel_health(device="dc-ce-1")
```

Expect `{ike_state: "READY", esp_state: "INSTALLED", peer: "<EIP>",
encrypted_packets: >0, decrypted_packets: >0, parser: "hand_rolled"}`.

If `ike_state: "UNKNOWN"`, the parser couldn't make sense of the device
output — capture `result["raw"]["ikev2"]` and add it to `tests/fixtures/`
as a regression sample.

### C. Via the Lambda (AWS-side view)

```bash
LAMBDA=$(terraform -chdir=$AWS_REPO/terraform/environments/dev \
  output -raw vpn_monitor_lambda_name)
INST=$(terraform -chdir=$AWS_REPO/terraform/environments/dev \
  output -raw strongswan_instance_id)

aws lambda invoke --function-name "$LAMBDA" /tmp/out.json && cat /tmp/out.json | jq
# expect: {"check_succeeded": true, "tunnel_established": 1, "state": "ESTABLISHED", ...}

aws cloudwatch get-metric-statistics \
  --namespace Lab/VPN --metric-name TunnelEstablished \
  --dimensions Name=InstanceId,Value=$INST \
  --start-time $(date -u -v-15M +%FT%TZ) --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Maximum
# expect Maximum == 1.0
```

### D. Data-plane integration test

```bash
EVE_LIVE=1 \
AWS_STRONGSWAN_EIP=$AWS_STRONGSWAN_EIP \
AWS_VPC_PROBE_IP=10.0.64.10 \
.venv/bin/python -m pytest tests/integration/test_cloud_tunnel_pipeline.py -v
```

`AWS_VPC_PROBE_IP` should be a reachable IP in one of the
`vpc_private_prefixes` outputs.

## Reboot survival

Reboot the strongSwan instance and confirm the tunnel returns:

```bash
aws ec2 reboot-instances --instance-ids "$INST"
sleep 180
ssh dc-ce-1 'show crypto ikev2 sa | include READY'
```

The `vpn-bootstrap.service` systemd unit re-fetches the PSK from Secrets
Manager and re-renders `/etc/swanctl/swanctl.conf` on every boot, so
reboot is non-destructive. If the tunnel doesn't return, see Troubleshooting.

## Security posture (what's enforced)

This deploy ships every fix from the security review:

| Layer | Enforcement |
|---|---|
| **Push-time PSK** | Plaintext PSK never committed; `scripts/push_configs._inject_aws_psk` fetches at deploy time. PSK character set excludes `"`, `$`, `` ` ``, `\` so heredoc rendering can't be subverted. |
| **Input validation** | `var.onprem_public_ip` rejects anything that isn't a literal IPv4 dotted quad — closes the shell-injection surface in user_data. |
| **Lambda IAM (M3)** | `ssm:SendCommand` granted ONLY on the strongSwan instance ARN AND a custom `aws_ssm_document` (`cloud-devops-vpn-monitor-list-sas`) that hardcodes `swanctl --list-sas`. `AWS-RunShellScript` is **not** in the policy — a future code change can't broaden blast radius without a Terraform diff. |
| **Lambda IAM (L7)** | `cloudwatch:GetMetricStatistics` and `ssm:GetCommandInvocation` conditioned on `aws:RequestedRegion = us-east-1` AND `aws:PrincipalAccount = <account>`. (NOTE: `aws:SourceAccount` does NOT work for direct API calls — that key only populates on service-to-service.) |
| **Lambda IAM (parity)** | `IAM_REQUIRED` constant in `handler.py` is statically asserted in lockstep with the Terraform IAM via the `test_iam_dependencies_documented` meta-test (AST-walks every `boto3.client(...).method(...)` call). Drift fails CI. |
| **State at rest** | S3 backend uses customer-managed KMS CMK (alias `cloud-devops-pipeline-tfstate-key`) with annual rotation. Bucket policy denies non-TLS uploads AND explicitly-non-KMS uploads (StringNotEqualsIfExists allows missing-header to fall through to default). DynamoDB lock table has PITR. |
| **EC2 metadata** | IMDSv2 only (`http_tokens = required`). Token theft via SSRF is prevented. |
| **Network scoping** | strongSwan SG ingress locked to `<onprem_public_ip>/32` (UDP 500 / UDP 4500 / ESP). IPsec traffic selectors scoped to VPC CIDR ↔ onprem CIDRs (NOT 0.0.0.0/0) so internet-bound traffic bypasses IPsec — keeps SSM agent connectivity alive. |
| **Reboot determinism** | `vpn-bootstrap.service` (systemd `Type=oneshot, Before=strongswan-starter.service`) re-fetches PSK and re-renders `/etc/swanctl/swanctl.conf` on every boot. |
| **Two-metric alarming** | `CheckSucceeded` and `TunnelEstablished` are separate metrics. Composite alarm `tunnel_down` fires on `Check=1 AND Established=0` via metric-math expression — a Lambda failure does NOT page on-call. Separate `lambda_broken` alarm on `CheckSucceeded` Sum < 1 over 30 min catches the Lambda itself. |

## Teardown

```bash
cd $AWS_REPO/terraform/environments/dev
terraform destroy -var enable_vpn=true -var onprem_public_ip=<same.IP> -var vpn_alert_email=<same.email>
```

Persistent state (survives destroy):
- S3 state bucket + KMS CMK + DynamoDB lock table (created by `bootstrap-state-backend.sh`)
- AWS Budget alarm (created manually)
- The lab side — Eve-NG_Agent repo, NetBox data, dc-ce-1 templates — untouched

The Secrets Manager secret enters a 7-day recovery window. Re-applying
within 7 days requires `aws secretsmanager delete-secret --secret-id
vpn/onprem-psk --force-delete-without-recovery` first.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `terraform apply` errors with `AccessDenied: explicit deny in resource-based policy` on state upload | Backend block missing `kms_key_id` (sends AES256 which the state-bucket policy denies) | Confirm `kms_key_id = "alias/cloud-devops-pipeline-tfstate-key"` is in the `backend "s3"` block; re-init with `-reconfigure` |
| `null_resource.strongswan_ready` times out at 5 min, cloud-init log shows `Failed to enable unit: Unit file strongswan-swanctl.service does not exist` | Wrong service name — Ubuntu 22.04's `strongswan-swanctl` package only ships the binary, not a service unit | The current user_data uses `strongswan-starter.service`; confirm via `cat terraform/modules/vpn/user_data.sh.tftpl \| grep enable` |
| Lambda returns `AccessDeniedException ... no identity-based policy allows ssm:GetCommandInvocation` | IAM condition uses `aws:SourceAccount` (which doesn't populate for direct API calls) | Confirm conditions use `aws:PrincipalAccount`; `terraform apply` to update |
| Lambda returns `ssm_error ... command stuck in Pending after 25s` then SSM agent goes `ConnectionLost` | IPsec traffic selectors are `0.0.0.0/0` — kernel xfrm policy captures ALL outbound traffic on the strongSwan box, including SSM agent's TLS connection | Scope `local_ts`/`remote_ts` in `swanctl.conf` to VPC CIDR ↔ onprem CIDRs; replace EC2 |
| `make push-configs` errors `IAM principal lacks secretsmanager:GetSecretValue` | Push host's IAM principal differs from the one that created the secret | Set `AWS_PROFILE`, or grant `secretsmanager:GetSecretValue` on `vpn/onprem-psk` to your principal |
| `make push-configs` errors `AWS_VPN_PSK_SECRET_ARN is not set` | Forgot to export from `.aws_outputs.json` | `export AWS_VPN_PSK_SECRET_ARN=$(jq -r .psk_secret_arn.value < .aws_outputs.json)` |
| `cloud_tunnel_health` returns `ike_state: "UNKNOWN"` | Hand-rolled parser couldn't match the device output | Capture `result["raw"]["ikev2"]`, file as a regression sample under `tests/fixtures/` |
| `show crypto ikev2 sa` shows `IN-NEG` then nothing | PSK mismatch between sides | Run [rotate-aws-vpn-psk.md](rotate-aws-vpn-psk.md) — most common cause is a stale `dc-ce-1` keyring after redeploying AWS |
| `show crypto ikev2 sa` shows `READY` but ping fails | Asymmetric routing (return path missing on AWS side) | `aws ec2 describe-route-tables --route-table-ids <private-rt>` — every onprem CIDR should point at the strongSwan ENI |
| Lambda `CheckSucceeded` consistently 0, SSM `Pending` | SSM agent on strongSwan unreachable (likely IPsec capture or SG/route issue) | SSM-Session into the box (`aws ssm start-session --target $INST`); check `journalctl -u amazon-ssm-agent`, `swanctl --list-policies` |
| Alarm fires for `tunnel-down` but `show crypto` says `READY` on `dc-ce-1` | strongSwan side down or `swanctl --list-sas` returns empty | SSM-Session, run `swanctl --list-sas`; check `journalctl -u vpn-bootstrap` and `journalctl -u strongswan-starter` |
| `terraform apply` blocked with `SecretAlreadyScheduledForDeletion` | Re-applying within 7 days of destroy | `aws secretsmanager delete-secret --secret-id vpn/onprem-psk --force-delete-without-recovery` then retry |

## Related

- [docs/runbooks/rotate-aws-vpn-psk.md](rotate-aws-vpn-psk.md) — manual PSK rotation
- [docs/lab-documentation.html](../lab-documentation.html) — full project visual overview, including the cloud-aws traffic flow
- `scripts/bootstrap-state-backend.sh` (in cloud-devops-pipeline) — state-backend hardening script (idempotent)
