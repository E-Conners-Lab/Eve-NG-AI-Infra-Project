# Runbook — Rotate the AWS VPN PSK

## When to use this

- Suspected PSK compromise.
- Periodic rotation per security policy.
- After any operator action that could have leaked the secret value (e.g. a
  console screenshot of `show running-config` on `dc-ce-1`).

## Why this is manual in v1

The Terraform `random_password.psk` resource has `lifecycle { ignore_changes = [keepers] }`
to prevent unintended PSK regeneration on routine `terraform apply` runs. PSK rotation
is a deliberate, two-sided operation: any change is asymmetric until BOTH the AWS-side
strongSwan and the lab-side `dc-ce-1` keyring are reloaded, and during that asymmetric
window the tunnel is down.

Automating this end-to-end is a follow-up. The manual sequence below is short and
testable; do it during a planned maintenance window.

## Prerequisites

- AWS CLI configured with credentials that have `secretsmanager:PutSecretValue`,
  `ssm:SendCommand` on the strongSwan instance, and `secretsmanager:GetSecretValue`.
- SSH or Netmiko access to `dc-ce-1` from this host.
- `AWS_VPN_PSK_SECRET_ARN` and `AWS_STRONGSWAN_EIP` exported (from `.aws_outputs.json`).

## Sequence

```bash
# 1. Generate a fresh PSK and write it to AWS Secrets Manager.
NEW_PSK=$(openssl rand -base64 36)
aws secretsmanager put-secret-value \
  --secret-id "$AWS_VPN_PSK_SECRET_ARN" \
  --secret-string "$NEW_PSK"

# 2. Tell the strongSwan instance to re-fetch the secret and reload swanctl.
#    The vpn-bootstrap.service unit re-renders /etc/swanctl/swanctl.conf on start.
STRONGSWAN_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=*strongswan*" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)
aws ssm send-command \
  --instance-ids "$STRONGSWAN_ID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl restart vpn-bootstrap.service"]'

# 3. Push dc-ce-1's config — _inject_aws_psk reads the new secret value
#    and replaces the marker in the rendered config in flight.
make push-configs DEVICE=dc-ce-1
```

## Convergence window

Between step 2 and step 3 the two ends carry different PSKs and IKE will
not negotiate. Expected outage: ~30 seconds, dominated by `make push-configs`
SSH + config-set time. Acceptable for the lab; not acceptable for production
without an automated push-on-change.

## Verify both ends converged

```bash
# Lab side — expect READY
ssh dc-ce-1 "show crypto ikev2 sa | include READY"

# AWS side — expect ESTABLISHED (or REKEYING during rekey window)
aws ssm send-command \
  --instance-ids "$STRONGSWAN_ID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["swanctl --list-sas"]'
# Then poll the result via aws ssm get-command-invocation.

# Via MCP tool (from Claude Code)
# cloud_tunnel_health(device="dc-ce-1")
# Expect: ike_state="READY", esp_state="INSTALLED", encrypted_packets > 0 within 30s
```

## Rollback

If step 1 succeeded but step 2 or 3 failed and the tunnel is down:

1. Restore the previous secret value:
   ```bash
   # Secrets Manager keeps prior versions for 30 days by default.
   aws secretsmanager list-secret-version-ids --secret-id "$AWS_VPN_PSK_SECRET_ARN"
   PREV_VERSION=<version-id from output, the one staged AWSPREVIOUS>
   aws secretsmanager update-secret-version-stage \
     --secret-id "$AWS_VPN_PSK_SECRET_ARN" \
     --version-stage AWSCURRENT \
     --move-to-version-id "$PREV_VERSION"
   ```
2. Re-run step 2 and step 3 to push the restored value to both sides.

## Known limitation

`scripts/push_configs.py` does not currently retry on transient SSH timeouts.
If step 3 fails partway through the IKE keyring lines, the keyring on
`dc-ce-1` is left in an unspecified state. Rerun `make push-configs DEVICE=dc-ce-1`
to retry; the template re-emits the full keyring block.
