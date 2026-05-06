"""Push generated configs to EVE-NG devices via SSH (Netmiko).

Reads rendered configs from configs/generated/ and pushes them to each
device over SSH. Every action is logged to a GAIT audit trail file.

Usage:
    python -m scripts.push_configs
    python -m scripts.push_configs --target dc_east
    python -m scripts.push_configs --device dc-spine-1
    python -m scripts.push_configs --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError, NoCredentialsError
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

logger = logging.getLogger(__name__)

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "generated"
GAIT_LOG_DIR = Path(__file__).parent.parent / "logs"

# Netmiko device_type per platform
NETMIKO_DEVICE_TYPE: dict[str, str] = {
    "arista_eos": "arista_eos",
    "cisco_iosxe": "cisco_xe",
    "fortinet_fortios": "fortinet",
}

# Marker the IPsec template emits in place of the actual PSK. Substituted at
# push time by _inject_aws_psk so the rendered config in configs/generated/
# stays commit-safe.
_AWS_VPN_PSK_MARKER = "__AWS_VPN_PSK__"
_AWS_VPN_PSK_ENV = "AWS_VPN_PSK_SECRET_ARN"


def _inject_aws_psk(config_text: str) -> str:
    """Replace the AWS VPN PSK marker with the secret value from AWS Secrets Manager.

    Pass-through if the marker is not present (most devices). When the marker
    is present, AWS_VPN_PSK_SECRET_ARN must be set, and the IAM principal
    running the push must have secretsmanager:GetSecretValue on that ARN.

    Returns the config text with the marker substituted. Each boto3 failure
    mode surfaces a unique RuntimeError message so operators can act fast.
    """
    if _AWS_VPN_PSK_MARKER not in config_text:
        return config_text

    secret_arn = os.environ.get(_AWS_VPN_PSK_ENV)
    if not secret_arn:
        raise RuntimeError(
            f"{_AWS_VPN_PSK_ENV} is not set but the rendered config contains "
            f"{_AWS_VPN_PSK_MARKER}. Run `make sync-aws-outputs` and export "
            f"{_AWS_VPN_PSK_ENV} from .aws_outputs.json before pushing."
        )

    client = boto3.client("secretsmanager")
    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except NoCredentialsError as exc:
        raise RuntimeError(
            "AWS credentials not available on push host — configure ~/.aws/credentials "
            "or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY before pushing."
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "AccessDeniedException":
            raise RuntimeError(
                f"IAM principal lacks secretsmanager:GetSecretValue on {secret_arn}"
            ) from exc
        if code == "ResourceNotFoundException":
            raise RuntimeError(
                f"secret ARN does not exist: {secret_arn} (check Terraform output)"
            ) from exc
        if code == "DecryptionFailure":
            raise RuntimeError(
                f"KMS key access denied while decrypting {secret_arn} — "
                "the IAM principal needs kms:Decrypt on the KMS key, too."
            ) from exc
        raise RuntimeError(f"AWS Secrets Manager error ({code}): {exc}") from exc

    psk_value = response.get("SecretString", "")
    if not psk_value:
        raise RuntimeError(
            f"secret {secret_arn} returned an empty SecretString — "
            "the secret may be binary; this push path expects SecretString."
        )

    return config_text.replace(_AWS_VPN_PSK_MARKER, psk_value)


def _gait_log(log_file: Path, entry: dict) -> None:
    """Append a GAIT audit entry to the log file."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _clean_config_lines(config_text: str, platform: str) -> list[str]:
    """Filter config lines for the target platform.

    Removes comments, blank lines, and platform-specific artifacts
    that cause push errors.
    """
    lines = config_text.splitlines()

    if platform == "arista_eos":
        return [
            line
            for line in lines
            if line.strip() and not line.strip().startswith("!") and line.strip() != "end"
        ]
    if platform == "cisco_iosxe":
        return [
            line
            for line in lines
            if line.strip() and not line.strip().startswith("!") and line.strip() != "end"
        ]
    if platform == "fortinet_fortios":
        filtered = [line for line in lines if line.strip() and not line.strip().startswith("###")]
        # Skip the HA config block — it kills SSH sessions when mode changes.
        # HA must be configured via console on first boot.
        # Track nesting depth so nested "config" / "end" pairs (e.g.,
        # "config ha-mgmt-interfaces" inside "config system ha") don't
        # prematurely exit the skip.
        result = []
        skip_depth = 0
        for line in filtered:
            stripped = line.strip()
            if skip_depth == 0 and stripped == "config system ha":
                skip_depth = 1
                continue
            if skip_depth > 0:
                if stripped.startswith("config "):
                    skip_depth += 1
                elif stripped == "end":
                    skip_depth -= 1
                continue
            result.append(line)
        return result
    return [line for line in lines if line.strip()]


def push_config_to_device(
    device_name: str,
    platform: str,
    config_path: Path,
    mgmt_ip: str,
    username: str,
    password: str,
    log_file: Path,
    dry_run: bool = False,
) -> bool:
    """Push a config file to a single device via Netmiko.

    Returns True on success, False on failure.
    """
    if not config_path.exists():
        print(f"  {device_name}: SKIP (no config file)")
        _gait_log(
            log_file,
            {
                "action": "skip",
                "device": device_name,
                "reason": "no config file",
            },
        )
        return False

    config_text = config_path.read_text()
    if not config_text.strip():
        print(f"  {device_name}: SKIP (empty config)")
        return False

    # Substitute the AWS VPN PSK marker if present (no-op for non-IPsec configs).
    # Done before _gait_log so an injection failure aborts the push without
    # logging "push_start" against a config we never actually sent.
    try:
        config_text = _inject_aws_psk(config_text)
    except RuntimeError as exc:
        print(f"  {device_name}: ERROR — PSK injection failed: {exc}")
        _gait_log(
            log_file,
            {
                "action": "push_error",
                "device": device_name,
                "error": f"psk_injection: {exc}",
            },
        )
        return False

    _gait_log(
        log_file,
        {
            "action": "push_start",
            "device": device_name,
            "platform": platform,
            "config_file": config_path.name,
            "config_lines": len(config_text.splitlines()),
            "mgmt_ip": mgmt_ip,
        },
    )

    if dry_run:
        print(
            f"  {device_name}: DRY RUN ({config_path.name}, {len(config_text.splitlines())} lines)"
        )
        _gait_log(log_file, {"action": "push_dry_run", "device": device_name})
        return True

    device_type = NETMIKO_DEVICE_TYPE.get(platform)
    if not device_type:
        print(f"  {device_name}: SKIP (no Netmiko driver for {platform})")
        _gait_log(
            log_file,
            {
                "action": "skip",
                "device": device_name,
                "reason": f"no Netmiko driver for {platform}",
            },
        )
        return False

    try:
        # FortiGate needs longer timeouts and no enable mode
        if platform == "fortinet_fortios":
            conn = ConnectHandler(
                device_type=device_type,
                host=mgmt_ip,
                username=username,
                password=password,
                timeout=30,
                read_timeout_override=30,
            )
        else:
            conn = ConnectHandler(
                device_type=device_type,
                host=mgmt_ip,
                username=username,
                password=password,
                secret=password,
                timeout=15,
            )
            conn.enable()
        _gait_log(log_file, {"action": "ssh_connected", "device": device_name})

        # Pre-push: save running config for rollback
        if platform in ("arista_eos", "cisco_iosxe"):
            backup = conn.send_command("show running-config")
            _gait_log(
                log_file,
                {
                    "action": "backup_saved",
                    "device": device_name,
                    "backup_lines": len(backup.splitlines()),
                },
            )

        # Clean config lines for platform
        clean_lines = _clean_config_lines(config_text, platform)

        # Push config — FortiGate uses write_channel (fire-and-forget)
        # because HA config changes cause continuous output that blocks
        # send_command_timing even with long timeouts.
        if platform == "fortinet_fortios":
            import time

            rejected_lines: list[str] = []
            for line in clean_lines:
                conn.write_channel(line + "\n")
                time.sleep(0.5)
                # Read back after each command to catch errors
                try:
                    response = conn.read_channel()
                    if response and any(
                        err in response.lower()
                        for err in (
                            "command fail",
                            "unknown action",
                            "node_check_object",
                            "entry not found",
                            "invalid",
                        )
                    ):
                        rejected_lines.append(f"{line.strip()} -> {response.strip()[:120]}")
                except Exception:
                    pass
            # Drain any remaining output
            time.sleep(2)
            with contextlib.suppress(Exception):
                conn.read_channel()

            if rejected_lines:
                _gait_log(
                    log_file,
                    {
                        "action": "push_fortios_rejected",
                        "device": device_name,
                        "rejected_count": len(rejected_lines),
                        "rejected": rejected_lines[:20],
                    },
                )
                logger.warning(
                    "%s: %d FortiOS commands rejected: %s",
                    device_name,
                    len(rejected_lines),
                    "; ".join(rejected_lines[:5]),
                )
        else:
            conn.send_config_set(clean_lines, cmd_verify=False)

        # Post-push: save to startup
        if platform in ("arista_eos", "cisco_iosxe"):
            conn.send_command("write memory")

        # Verify hostname
        if platform == "arista_eos":
            verify = conn.send_command("show hostname")
        elif platform == "cisco_iosxe":
            verify = conn.send_command("show running-config | include hostname")
        elif platform == "fortinet_fortios":
            # Skip verify — HA changes may have disrupted the session
            verify = "FortiGate push complete (no verify — HA may restart)"
        else:
            verify = ""

        conn.disconnect()

        _gait_log(
            log_file,
            {
                "action": "push_success",
                "device": device_name,
                "config_lines": len(clean_lines),
                "verify_snippet": verify.strip()[:100],
            },
        )
        print(f"  {device_name}: OK ({len(clean_lines)} lines)")
        return True

    except (NetmikoAuthenticationException, NetmikoTimeoutException) as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"  {device_name}: ERROR — {error_msg}")
        _gait_log(
            log_file,
            {
                "action": "push_error",
                "device": device_name,
                "error": error_msg,
            },
        )
        return False
    except Exception as e:
        logger.exception("Error on device: %s", e)
        error_msg = f"{type(e).__name__}: {e}"
        print(f"  {device_name}: ERROR — {error_msg}")
        _gait_log(
            log_file,
            {
                "action": "push_error",
                "device": device_name,
                "error": error_msg,
            },
        )
        return False


def push_all_configs(
    spec: dict,
    configs_dir: Path,
    target: str = "all",
    device_filter: str = "",
    dry_run: bool = False,
) -> tuple[int, int]:
    """Push configs to all devices. Returns (success_count, fail_count)."""
    creds = require_credentials("device_username", "device_password")
    mgmt_ips = get_mgmt_ips()

    log_file = GAIT_LOG_DIR / f"push_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"
    _gait_log(
        log_file,
        {
            "action": "push_session_start",
            "target": target,
            "device_filter": device_filter,
            "dry_run": dry_run,
        },
    )

    # Collect devices to push
    all_devices: list[dict] = []
    site_map = {"dc_east": "dc-east", "branch_01": "branch-01", "dr_west": "dr-west"}

    for spec_key, site in spec.get("sites", {}).items():
        site_slug = site_map.get(spec_key, "")
        for dev in site.get("devices", []):
            if dev["role"] != "host":
                all_devices.append({**dev, "_site": site_slug})
    for dev in spec.get("wan_transport", {}).get("devices", []):
        all_devices.append({**dev, "_site": "wan"})
    for zone_key, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            all_devices.append({**dev, "_site": zone_key})

    # Filter by target site or specific device
    if device_filter:
        all_devices = [d for d in all_devices if d["name"] == device_filter]
    elif target != "all":
        target_slug = site_map.get(target, target)
        all_devices = [d for d in all_devices if d["_site"] == target_slug]

    success, fail = 0, 0
    print(f"Pushing configs to {len(all_devices)} devices:")

    for dev in all_devices:
        name = dev["name"]
        platform = dev["platform"]
        mgmt_ip = mgmt_ips.get(name, "")

        if not mgmt_ip:
            print(f"  {name}: SKIP (no management IP)")
            fail += 1
            continue

        config_path = configs_dir / f"{name}.cfg"

        # Use FortiGate creds if applicable
        if platform == "fortinet_fortios":
            username = creds.fortigate_username or creds.device_username
            password = creds.fortigate_password or creds.device_password
        else:
            username = creds.device_username
            password = creds.device_password

        ok = push_config_to_device(
            name, platform, config_path, mgmt_ip, username, password, log_file, dry_run
        )
        if ok:
            success += 1
        else:
            fail += 1

    _gait_log(
        log_file,
        {
            "action": "push_session_end",
            "success": success,
            "fail": fail,
        },
    )
    print(f"\nResults: {success} success, {fail} failed")
    print(f"GAIT log: {log_file}")
    return success, fail


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Push configs to EVE-NG devices")
    parser.add_argument(
        "--target",
        default="all",
        choices=["all", "dc_east", "branch_01", "dr_west"],
        help="Target site (default: all)",
    )
    parser.add_argument("--device", default="", help="Push to a single device by name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without pushing")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--configs", type=Path, default=CONFIGS_DIR)
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())
    success, fail = push_all_configs(spec, args.configs, args.target, args.device, args.dry_run)
    sys.exit(1 if fail > 0 else 0)


if __name__ == "__main__":
    main()
