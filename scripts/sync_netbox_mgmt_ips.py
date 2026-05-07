"""Sync management IPs from agent/testbed.yaml into NetBox.

For each device in the testbed, ensure NetBox has:
  1. A Management1 interface on the device.
  2. An IP address object for the mgmt IP.
  3. The IP attached to the Management1 interface.
  4. The device's primary_ip4 set to that IP.

Idempotent — safe to re-run after lab redeploys or IP changes. Reads
NetBox creds from .env (or env vars). Skips devices not yet present
in NetBox (run populate_netbox.py first).

Usage:
    python -m scripts.sync_netbox_mgmt_ips
    python -m scripts.sync_netbox_mgmt_ips --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pynetbox
import yaml

from scripts.credentials import require_credentials

TESTBED_PATH = Path(__file__).parent.parent / "agent" / "testbed.yaml"
MGMT_INTERFACE_NAME = "Management1"
MGMT_PREFIX = "192.168.68.0/22"


def _testbed_devices(testbed_path: Path) -> dict[str, str]:
    """Return {device_name: mgmt_ip} from the testbed file."""
    tb = yaml.safe_load(testbed_path.read_text())
    out: dict[str, str] = {}
    for name, dev in (tb.get("devices") or {}).items():
        ip = (dev.get("connections") or {}).get("cli", {}).get("ip")
        if ip:
            out[name] = ip
    return out


def _ensure_interface(nb, device, name: str):
    """Get or create an interface named `name` on `device`."""
    existing = nb.dcim.interfaces.get(device_id=device.id, name=name)
    if existing:
        return existing
    return nb.dcim.interfaces.create(
        {
            "device": device.id,
            "name": name,
            "type": "virtual",
            "mgmt_only": True,
            "description": "Out-of-band management",
        }
    )


def _ensure_ip(nb, address_with_mask: str, interface):
    """Get or create an IP, ensure it's attached to `interface`."""
    existing = nb.ipam.ip_addresses.get(address=address_with_mask)
    if existing is None:
        return nb.ipam.ip_addresses.create(
            {
                "address": address_with_mask,
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface.id,
                "description": "Management IP (synced from testbed.yaml)",
            }
        )
    wrong_type = existing.assigned_object_type != "dcim.interface"
    wrong_id = existing.assigned_object_id != interface.id
    if wrong_type or wrong_id:
        existing.assigned_object_type = "dcim.interface"
        existing.assigned_object_id = interface.id
        existing.save()
    return existing


def _ensure_primary_ip(device, ip_obj) -> bool:
    """Set device.primary_ip4 if not already pointing at this IP. Return True if changed."""
    current = getattr(device, "primary_ip4", None)
    current_id = current.id if current else None
    if current_id == ip_obj.id:
        return False
    device.primary_ip4 = ip_obj.id
    device.save()
    return True


def _ensure_mgmt_prefix(nb, prefix: str) -> None:
    """Create the management prefix if it doesn't exist (so IPs aren't orphaned)."""
    if nb.ipam.prefixes.get(prefix=prefix) is None:
        nb.ipam.prefixes.create(
            {
                "prefix": prefix,
                "status": "active",
                "description": "Lab management network (LAN-bridged from EVE-NG)",
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync mgmt IPs from testbed.yaml to NetBox")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--testbed", type=Path, default=TESTBED_PATH)
    args = parser.parse_args()

    if not args.testbed.exists():
        print(f"ERROR: Testbed not found: {args.testbed}", file=sys.stderr)
        sys.exit(1)

    creds = require_credentials("netbox_url", "netbox_token")
    print(f"Connecting to NetBox at {creds.netbox_url}...")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)

    devices = _testbed_devices(args.testbed)
    print(f"Loaded {len(devices)} devices from testbed.\n")

    if args.dry_run:
        for name, ip in sorted(devices.items()):
            print(f"  [dry-run] would set {name:20s} primary_ip4 = {ip}/22")
        return

    _ensure_mgmt_prefix(nb, MGMT_PREFIX)

    summary = {"updated": 0, "already_set": 0, "missing_in_netbox": 0}
    for name, ip in sorted(devices.items()):
        device = nb.dcim.devices.get(name=name)
        if device is None:
            print(f"  SKIP {name:20s} (not in NetBox — run populate_netbox.py first)")
            summary["missing_in_netbox"] += 1
            continue

        iface = _ensure_interface(nb, device, MGMT_INTERFACE_NAME)
        ip_obj = _ensure_ip(nb, f"{ip}/22", iface)
        changed = _ensure_primary_ip(device, ip_obj)
        if changed:
            summary["updated"] += 1
            print(f"  UPDATE {name:20s} primary_ip4 -> {ip}")
        else:
            summary["already_set"] += 1
            print(f"  OK     {name:20s} primary_ip4 = {ip}")

    print("\nSync complete:")
    for k, v in summary.items():
        print(f"  {k:20s} {v}")


if __name__ == "__main__":
    main()
