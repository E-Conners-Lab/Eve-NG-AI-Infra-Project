"""Bootstrap management IPs on EVE-NG devices via startup-config injection.


Solves the chicken-and-egg problem: we need SSH to push configs, but devices
have no management IPs until configs are pushed. This script generates minimal
startup configs with ONLY the management interface IP and injects them into
EVE-NG nodes via the API — no SSH needed.

After running this script, start the nodes and they'll come up with management
connectivity. Then use push_configs.py to push the full configuration.

Usage:
    python -m scripts.bootstrap_mgmt
    python -m scripts.bootstrap_mgmt --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

logger = logging.getLogger(__name__)

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"

# Minimal startup config templates per platform — ONLY management IP + SSH.
MGMT_TEMPLATES: dict[str, str] = {
    "arista_eos": """!
hostname {hostname}
!
interface Management1
   ip address {mgmt_ip}/22
   no shutdown
!
ip routing
!
management api http-commands
   no shutdown
!
management ssh
   idle-timeout 60
!
username admin privilege 15 role network-admin secret {password}
!
end
""",
    "cisco_iosxe": """!
! Bypass initial config dialog and password change prompt
service password-encryption
no service config
no platform punt-keepalive disable-kernel-core
!
hostname {hostname}
!
security passwords min-length 1
!
username {username} privilege 15 secret 0 {password}
!
interface GigabitEthernet5
 ip address {mgmt_ip} 255.255.252.0
 no shutdown
!
ip route 0.0.0.0 0.0.0.0 {gateway}
ip ssh version 2
ip scp server enable
!
line con 0
 login local
 stopbits 1
line vty 0 4
 login local
 transport input ssh
!
end
""",
    "fortinet_fortios": """config system global
    set hostname "{hostname}"
end

config system interface
    edit "port6"
        set ip {mgmt_ip} 255.255.252.0
        set allowaccess ping https ssh
    next
end

config system admin
    edit "{username}"
        set password {password}
    next
end
""",
    "linux": """# Alpine Linux management bootstrap
auto eth1
iface eth1 inet static
    address {mgmt_ip}/22
    gateway {gateway}
""",
}


def _build_mgmt_config(
    hostname: str,
    platform: str,
    mgmt_ip: str,
    gateway: str,
    username: str,
    password: str,
) -> str:
    """Generate a minimal management-only startup config."""
    template = MGMT_TEMPLATES.get(platform, "")
    if not template:
        return ""
    return template.format(
        hostname=hostname,
        mgmt_ip=mgmt_ip,
        gateway=gateway,
        username=username,
        password=password,
    )


def bootstrap_management(
    spec: dict,
    dry_run: bool = False,
) -> None:
    """Inject management startup configs into EVE-NG nodes via API."""
    mgmt_ips = get_mgmt_ips()
    creds = require_credentials(
        "eve_ng_host", "eve_ng_password", "device_username", "device_password"
    )

    from evengsdk.api import EvengApi
    from evengsdk.client import EvengClient as SdkClient

    sdk = SdkClient(creds.eve_ng_host, log_level="WARNING", ssl_verify=False, protocol="https")
    if not dry_run:
        sdk.login(username=creds.eve_ng_username, password=creds.eve_ng_password)
    eve = EvengApi(sdk) if not dry_run else None

    lab_path = "/AI-Infra-Lab"
    gateway = "192.168.68.1"

    # Collect all network devices
    all_devs: list[dict] = []
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["role"] != "host":
                all_devs.append(dev)
    for dev in spec.get("wan_transport", {}).get("devices", []):
        all_devs.append(dev)
    for _zk, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            all_devs.append(dev)
    # Also include hosts (they need management for reachability tests)
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["role"] == "host":
                all_devs.append(dev)

    print(f"Bootstrapping management IPs on {len(all_devs)} devices:\n")
    injected = 0

    for dev in all_devs:
        name = dev["name"]
        platform = dev["platform"]
        mgmt_ip = mgmt_ips.get(name, "")

        if not mgmt_ip:
            print(f"  {name:<15s} — SKIP (no management IP)")
            continue

        config = _build_mgmt_config(
            name,
            platform,
            mgmt_ip,
            gateway,
            creds.device_username,
            creds.device_password,
        )
        if not config:
            print(f"  {name:<15s} — SKIP (no template for {platform})")
            continue

        print(f"  {name:<15s} {mgmt_ip}", end="")

        if dry_run:
            print(f" — DRY RUN ({len(config.splitlines())} lines)")
            injected += 1
            continue

        try:
            # Find the node by name
            node = eve.get_node_by_name(lab_path, name)
            if not node:
                print(" — ERROR (node not found in EVE-NG)")
                continue

            node_id = str(node["id"])

            # Step 1: Upload startup config to EVE-NG's config store
            eve.upload_node_config(lab_path, node_id, config)

            # Step 2: Enable the config so EVE-NG applies it on next boot
            eve.enable_node_config(lab_path, node_id)

            print(" — OK")
            injected += 1
        except Exception as e:
            logger.exception("Error: %s", e if "e" in dir() else "unknown")
            print(f" — ERROR: {e}")

    if not dry_run:
        sdk.logout()

    print(f"\nBootstrapped {injected}/{len(all_devs)} devices.")
    if not dry_run:
        print("Now stop all nodes, wipe them, and start again.")
        print("They will boot with management IPs configured.")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Bootstrap management IPs via EVE-NG API")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())
    bootstrap_management(spec, args.dry_run)


if __name__ == "__main__":
    main()
