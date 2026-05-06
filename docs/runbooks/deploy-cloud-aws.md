# Runbook — Deploy the Cloud-AWS Hybrid Site

## What this builds

A site-to-site IPsec tunnel from `dc-ce-1` (Cisco C8000v in EVE-NG) to a
self-managed strongSwan EC2 in AWS, plus a scheduled Lambda that watches
the tunnel from the AWS side. End state:

- **AWS side** — `t3.micro` strongSwan EC2 with EIP, Secrets Manager-backed
  PSK, IAM-scoped Lambda monitor, two CloudWatch alarms (tunnel-down vs.
  lambda-broken), SNS email alerts.
- **Lab side** — a `cloud_aws` site in NetBox, an IPsec block on `dc-ce-1`
  rendered from the spec, an MCP tool (`cloud_tunnel_health`) that reports
  IKEv2/IPsec state.
- **Pipeline** — push-time PSK injection (`scripts/push_configs.py`) pulls
  the secret from AWS at deploy time so `configs/generated/dc-ce-1.cfg`
  stays commit-safe.

## Prerequisites

- AWS account with the free tier active (or a budget you're OK spending).
  The `vpn` module fits inside the 12-month free tier; the existing
  `cloud-devops-pipeline` `dev` env does **not** (NAT GW + ALB + RDS).
- `cloud-devops-pipeline` repo cloned at a known path (referred to here as
  `$AWS_REPO`).
- Terraform ≥ 1.5, AWS CLI v2, IAM creds with permission to create EC2,
  EIP, Secrets Manager, IAM, Lambda, CloudWatch, SNS, EventBridge, and
  modify the existing VPC's private route table.
- The lab is already populated: `make generate-spec` succeeds against the
  21-node baseline, `dc-ce-1` is reachable via SSH from this host, NetBox
  is up.
- A reachable public IP for IKE ingress (your home/office uplink, or the
  Proxmox host's public IP). The strongSwan SG only allows UDP 500/4500/ESP
  from this `/32`.

## Cost expectations

| Resource | Monthly cost |
|---|---|
| `t3.micro` strongSwan (12-month free tier) | $0 |
| EIP (attached) | $0 (charged $3.60/mo only when **detached**) |
| Lambda @ rate(5 min) ≈ 8.6k invocations | $0 (1M invocations free) |
| CloudWatch — 2 alarms + custom metrics | $0 (10 metrics + 10 alarms free) |
| SNS email | $0 (1k notifications free) |
| Secrets Manager | $0.40 + ~$0 API calls |

**Heads-up:** the existing `cloud-devops-pipeline dev` env runs a NAT
Gateway (~$32/mo) and ALB (~$16/mo). If you only want the hybrid VPN demo
and not the full pipeline, run `terraform destroy` for the other modules
between sessions, or split the env.

Set a $1/mo Budget alarm on first apply (one-time):

```bash
aws budgets create-budget \
  --account-id $(aws sts get-caller-identity --query Account --output text) \
  --budget '{"BudgetName":"vpn-lab-1usd","BudgetLimit":{"Amount":"1","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"YOU@example.com"}]}]'
```

## One-time setup

```bash
# 1. Lab side: confirm .env exposes the AWS-coupling vars (see .env.example).
grep -E '^(AWS_REPO|ONPREM_PUBLIC_IP)=' .env || echo "fill these in"

# 2. Export the AWS repo path so `make sync-aws-outputs` can find it.
export AWS_REPO=/Users/<you>/PycharmProjects/cloud-devops-pipeline

# 3. AWS side: confirm CLI auth.
aws sts get-caller-identity
```

## Build sequence

The full deploy is six commands. Time budget: ~6–8 minutes (most of which
is the EC2 user_data installing strongSwan from EPEL — the
`null_resource.strongswan_ready` ready-gate makes Terraform wait for it).

### 1. Apply AWS side

```bash
cd $AWS_REPO/terraform/environments/dev
terraform init   # if not already
terraform apply \
  -var enable_vpn=true \
  -var onprem_public_ip=<your.public.ip> \
  -var vpn_alert_email=<you@example.com>
```

Confirm the `null_resource.strongswan_ready` ran green at the end of
apply — the `local-exec` polls SSM until `systemctl is-active strongswan`
returns `active`. If apply succeeds without that step printing, the gate
was bypassed; re-check the `enable_vpn=true` flag.

You'll get an SNS subscription-confirm email — click it. Without
confirmation the alerts fire silently.

### 2. Pull AWS outputs into the lab

```bash
cd $LAB_REPO        # back to Eve-NG_Agent
make sync-aws-outputs
```

This writes `.aws_outputs.json` (gitignored — contains the strongSwan EIP
and PSK secret ARN, not the secret itself).

Export the values into your shell so `populate_cloud_aws` and
`push-configs` can reach them without re-reading the file:

```bash
export AWS_STRONGSWAN_EIP=$(jq -r .strongswan_eip.value < .aws_outputs.json)
export AWS_VPN_PSK_SECRET_ARN=$(jq -r .psk_secret_arn.value < .aws_outputs.json)
```

### 3. Seed NetBox with the cloud-aws site

```bash
make seed-cloud-aws
```

Idempotent — safe to re-run after an `apply` that changed any of the
outputs. The script swaps AWS-perspective inner addresses to CE-perspective
when writing `dc-ce-1`'s `vpn_tunnels` config_context.

### 4. Regenerate spec + configs

```bash
make generate-spec
make generate-configs
```

`specs/generated/lab_spec.yaml` now contains a `cloud_aws` site with one
`vpn_tunnel`. `configs/generated/dc-ce-1.cfg` contains the IPsec block
with the literal marker `__AWS_VPN_PSK__` where the keyring PSK belongs.

Sanity-check the marker is present and no plaintext PSK leaked in:

```bash
grep -c '__AWS_VPN_PSK__' configs/generated/dc-ce-1.cfg   # expect: 1
grep -c 'pre-shared-key ' configs/generated/dc-ce-1.cfg   # expect: 1
```

### 5. Validate

```bash
make validate-batfish
```

**Known gap:** Batfish does not model Cisco IKEv2/IPsec crypto blocks, so
a green `validate-batfish` does **not** confirm the tunnel will come up.
This step is still worth running for the BGP and reachability checks on
the rest of the lab. Ground truth for IPsec state comes from
`cloud_tunnel_health` after push (see step 7).

### 6. Push to dc-ce-1

```bash
make push-configs DEVICE=dc-ce-1
```

`scripts/push_configs._inject_aws_psk` reads `AWS_VPN_PSK_SECRET_ARN`,
fetches the secret from AWS at this exact moment, substitutes the marker
in memory, and sends to the device. The plaintext PSK never lands on
disk; it only exists transiently in the push process and on `dc-ce-1`'s
running config (where it ends up type-7-encoded — known limitation, see
the [PSK rotation runbook](rotate-aws-vpn-psk.md)).

## Verify (four independent signals)

The plan calls for cross-verification because each method can lie in a
different way. All four should agree.

### A. On the device

```bash
ssh dc-ce-1 'show crypto ikev2 sa | include READY'
ssh dc-ce-1 'show crypto ipsec sa | include encrypt|decrypt'
```

Expect `READY` for IKEv2 within 30 seconds of push, and non-zero
`#pkts encrypt` / `#pkts decrypt` after a few seconds of probe traffic.

### B. Via the MCP tool (agent's view)

From Claude Code or the FastMCP client:

```
cloud_tunnel_health(device="dc-ce-1")
```

Expect `{ike_state: "READY", esp_state: "INSTALLED", peer: "<EIP>",
encrypted_packets: >0, decrypted_packets: >0, parser: "hand_rolled"}`.

If you get `ike_state: "UNKNOWN"`, the hand-rolled parser couldn't make
sense of the device output — capture `result["raw"]["ikev2"]` and add it
to `tests/fixtures/` as a regression sample.

### C. Via the Lambda (AWS-side view)

```bash
LAMBDA=$(terraform -chdir=$AWS_REPO/terraform/environments/dev \
  output -raw vpn_monitor_lambda_name)
INST=$(terraform -chdir=$AWS_REPO/terraform/environments/dev \
  output -raw strongswan_instance_id)

aws lambda invoke --function-name "$LAMBDA" /tmp/out.json && cat /tmp/out.json
# expect: {"check_succeeded": true, "tunnel_established": 1, "state": "ESTABLISHED", ...}

aws cloudwatch get-metric-statistics \
  --namespace Lab/VPN --metric-name TunnelEstablished \
  --dimensions Name=InstanceId,Value=$INST \
  --start-time $(date -u -v-15M +%FT%TZ) --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Maximum
# expect Maximum == 1.0

aws cloudwatch get-metric-statistics \
  --namespace Lab/VPN --metric-name CheckSucceeded \
  --dimensions Name=InstanceId,Value=$INST \
  --start-time $(date -u -v-15M +%FT%TZ) --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Maximum
# expect Maximum == 1.0  (Lambda itself is healthy)
```

### D. Data-plane integration test

```bash
EVE_LIVE=1 \
AWS_STRONGSWAN_EIP=$AWS_STRONGSWAN_EIP \
AWS_VPC_PROBE_IP=10.0.64.10 \
.venv/bin/python -m pytest tests/integration/test_cloud_tunnel_pipeline.py -v
```

`AWS_VPC_PROBE_IP` should be a reachable IP in one of the
`vpc_private_prefixes` outputs — easiest is to spin up a temporary
`t3.nano` in the private subnet for the test, then terminate it.

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

## Teardown

Free-tier hygiene — destroy when you're done:

```bash
cd $AWS_REPO/terraform/environments/dev
terraform destroy \
  -var enable_vpn=true \
  -var onprem_public_ip=<the same IP you applied with>
```

Side-effects to be aware of:
- The Secrets Manager secret has a 7-day recovery window (set in
  `modules/vpn/main.tf`). If you re-apply within 7 days using the same
  name, you'll hit `InvalidRequestException: SecretAlreadyScheduledForDeletion`.
  Either wait, or `aws secretsmanager restore-secret --secret-id vpn/onprem-psk`
  before re-apply.
- The lab side does **not** auto-clean — `dc-ce-1`'s IPsec config and the
  `cloud-aws` NetBox site persist. Re-run `make seed-cloud-aws` (with a
  fresh `.aws_outputs.json`) when you re-apply; it's idempotent and will
  update the EIP/PSK ARN in place.
- The PSK in `dc-ce-1`'s running config is now stale. The next
  `make push-configs DEVICE=dc-ce-1` will re-render with the new marker
  and a fresh PSK fetch.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `terraform apply` hangs at `null_resource.strongswan_ready` then errors after 5 min | EPEL install failed (regional repo issue) or SSM agent not connected | SSM-Session into the box (`aws ssm start-session --target $INST`), check `journalctl -u cloud-init`, fix and `terraform taint module.vpn[0].aws_instance.strongswan` then re-apply |
| `make push-configs` errors `IAM principal lacks secretsmanager:GetSecretValue` | The shell running the push is using a different IAM principal than the one that created the secret | Either run `aws configure sso` / set `AWS_PROFILE`, or attach `secretsmanager:GetSecretValue` on `vpn/onprem-psk` to your principal |
| `make push-configs` errors `AWS_VPN_PSK_SECRET_ARN is not set but the rendered config contains __AWS_VPN_PSK__` | Forgot to export from `.aws_outputs.json` | `export AWS_VPN_PSK_SECRET_ARN=$(jq -r .psk_secret_arn.value < .aws_outputs.json)` |
| `cloud_tunnel_health` returns `ike_state: "UNKNOWN"` | Hand-rolled parser couldn't match the device output | Capture `result["raw"]["ikev2"]`, file it as a regression sample under `tests/fixtures/`, extend `agent/skills/cloud_tunnel_health/skill.py:parse_ikev2_sa_detail` |
| `show crypto ikev2 sa` shows `IN-NEG` then nothing | PSK mismatch between sides | Run the [PSK rotation runbook](rotate-aws-vpn-psk.md) — the most common cause is stepping on a stale `dc-ce-1` keyring after redeploying AWS |
| `show crypto ikev2 sa` shows `READY` but ping fails | Asymmetric routing (return path missing on the AWS side) | Check `aws ec2 describe-route-tables --route-table-ids $(terraform output -raw private_route_table_id)` — every onprem CIDR should point at the strongSwan ENI |
| Lambda CloudWatch metric `CheckSucceeded` is consistently 0 | SSM agent on the strongSwan box is unreachable, or the Lambda role lost `ssm:SendCommand` on the document ARN | `aws logs tail /aws/lambda/cloud-devops-vpn-monitor` — error message identifies which API failed |
| SNS alarm fires for `cloud-devops-vpn-monitor-tunnel-down` but `show crypto` says `READY` on `dc-ce-1` | Either the strongSwan side is down or `swanctl --list-sas` returns empty (instance still booting) | SSM-Session into the box and run `swanctl --list-sas` directly; check `journalctl -u vpn-bootstrap` and `journalctl -u strongswan` |

## Related runbooks

- [Rotate the AWS VPN PSK](rotate-aws-vpn-psk.md) — when the PSK is
  compromised or due for rotation.
