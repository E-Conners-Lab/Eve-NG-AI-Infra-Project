"""Push generated configs to EVE-NG devices via SSH (Scrapli).

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
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "generated"
GAIT_LOG_DIR = Path(__file__).parent.parent / "logs"

# Scrapli platform mapping
SCRAPLI_PLATFORM: dict[str, str] = {
    "arista_eos": "arista_eos",
    "cisco_iosxe": "cisco_iosxe",
}

# FortiGate and Linux use scrapli_community or generic SSH
COMMUNITY_PLATFORMS = {"fortinet_fortios", "linux"}


def _gait_log(log_file: Path, entry: dict) -> None:
    """Append a GAIT audit entry to the log file."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _get_scrapli_connection(
    device_name: str,
    platform: str,
    mgmt_ip: str,
    username: str,
    password: str,
) -> Scrapli | None:
    """Create a Scrapli connection object for a device."""
    scrapli_platform = SCRAPLI_PLATFORM.get(platform)

    if scrapli_platform:
        return Scrapli(
            host=mgmt_ip,
            auth_username=username,
            auth_password=password,
            auth_strict_key=False,
            platform=scrapli_platform,
            transport="system",
        )

    if platform == "fortinet_fortios":
        return Scrapli(
            host=mgmt_ip,
            auth_username=username,
            auth_password=password,
            auth_strict_key=False,
            platform="fortinet_fortios",
            transport="system",
            transport_options={},
        )

    return None


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
    """Push a config file to a single device.

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

    conn = _get_scrapli_connection(device_name, platform, mgmt_ip, username, password)
    if not conn:
        print(f"  {device_name}: SKIP (no Scrapli driver for {platform})")
        _gait_log(
            log_file,
            {
                "action": "skip",
                "device": device_name,
                "reason": f"no driver for {platform}",
            },
        )
        return False

    try:
        conn.open()
        _gait_log(log_file, {"action": "ssh_connected", "device": device_name})

        # Send config based on platform
        if platform == "arista_eos" or platform == "cisco_iosxe":
            result = conn.send_configs(config_text.splitlines())
        elif platform == "fortinet_fortios":
            # FortiGate uses a different config mode
            result = conn.send_commands(config_text.splitlines())
        else:
            result = conn.send_commands(config_text.splitlines())

        failed = getattr(result, "failed", False)
        if failed:
            print(f"  {device_name}: FAILED")
            _gait_log(
                log_file,
                {
                    "action": "push_failed",
                    "device": device_name,
                    "error": str(getattr(result, "result", "")),
                },
            )
            return False

        # Verify by reading back hostname
        verify = conn.send_command("show hostname" if platform == "arista_eos" else "show version")
        _gait_log(
            log_file,
            {
                "action": "push_success",
                "device": device_name,
                "config_lines": len(config_text.splitlines()),
                "verify_output_length": len(verify.result),
            },
        )

        conn.close()
        print(f"  {device_name}: OK ({len(config_text.splitlines())} lines)")
        return True

    except ScrapliException as e:
        print(f"  {device_name}: ERROR — {e}")
        _gait_log(
            log_file,
            {
                "action": "push_error",
                "device": device_name,
                "error": str(e),
            },
        )
        return False
    finally:
        import contextlib

        with contextlib.suppress(Exception):
            conn.close()


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
