"""Create the 21-node EVE-NG lab topology from the YAML spec.

Uses the evengsdk library for all EVE-NG API operations. This ensures
correct interface name resolution, lab file locking, and bridge creation.

Usage:
    python -m scripts.create_topology
    python -m scripts.create_topology --lab-name "AI-Infra-Lab"
    python -m scripts.create_topology --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
LAB_NAME = "AI-Infra-Lab"

# EVE-NG template + image mapping per platform.
PLATFORM_MAP: dict[str, dict] = {
    "arista_eos": {
        "template": "veos",
        "image": "veos-4.33.1.1F",
        "ram": 2048,
        "cpu": 1,
        "ethernet": 8,
    },
    "cisco_iosxe": {
        "template": "c8000v",
        "image": "c8000v-17.13.01a",
        "ram": 4096,
        "cpu": 1,
        "ethernet": 5,  # 4 data (Gi1-Gi4) + 1 mgmt (Gi5)
    },
    "fortinet_fortios": {
        "template": "fortinet",
        "image": "fortinet-FGT",
        "ram": 2048,
        "cpu": 1,
        "ethernet": 6,
    },
    "linux": {
        "template": "linux",
        "image": "linux-alpine",
        "ram": 512,
        "cpu": 1,
        "ethernet": 2,
    },
}

# Management interface name per platform (EVE-NG short label).
MGMT_IFACE_LABEL: dict[str, str] = {
    "arista_eos": "Mgmt1",
    "cisco_iosxe": "Gi5",
    "fortinet_fortios": "port6",
    "linux": "e1",
}

# Management network IP assignments (192.168.68.110-130).
MGMT_IPS: dict[str, str] = {
    "dc-spine-1": "192.168.68.110",
    "dc-spine-2": "192.168.68.111",
    "dc-leaf-1": "192.168.68.112",
    "dc-leaf-2": "192.168.68.113",
    "dc-border-1": "192.168.68.114",
    "dc-border-2": "192.168.68.115",
    "dc-host-1": "192.168.68.116",
    "dc-host-2": "192.168.68.117",
    "dc-fw-1": "192.168.68.118",
    "dc-fw-2": "192.168.68.119",
    "dc-ce-1": "192.168.68.120",
    "sp-pe-1": "192.168.68.121",
    "sp-pe-2": "192.168.68.122",
    "br-ce-1": "192.168.68.123",
    "br-host-1": "192.168.68.124",
    "dr-leaf-1": "192.168.68.125",
    "dr-leaf-2": "192.168.68.126",
    "dr-fw-1": "192.168.68.127",
    "dr-fw-2": "192.168.68.128",
    "dr-ce-1": "192.168.68.129",
    "dr-host-1": "192.168.68.130",
}

# Visual layout positions (left, top) for the EVE-NG canvas.
LAYOUT: dict[str, tuple[int, int]] = {
    "dc-spine-1": (300, 50),
    "dc-spine-2": (500, 50),
    "dc-leaf-1": (200, 200),
    "dc-leaf-2": (400, 200),
    "dc-border-1": (600, 200),
    "dc-border-2": (800, 200),
    "dc-host-1": (200, 350),
    "dc-host-2": (400, 350),
    "dc-fw-1": (600, 350),
    "dc-fw-2": (800, 350),
    "dc-ce-1": (700, 500),
    "sp-pe-1": (500, 650),
    "sp-pe-2": (900, 650),
    "br-ce-1": (300, 800),
    "br-host-1": (300, 950),
    "dr-leaf-1": (1100, 200),
    "dr-leaf-2": (1300, 200),
    "dr-fw-1": (1100, 350),
    "dr-fw-2": (1300, 350),
    "dr-ce-1": (1200, 500),
    "dr-host-1": (1200, 800),
}


def _collect_all_devices(spec: dict) -> list[dict]:
    """Collect all devices from every section of the spec."""
    devices = []
    for _site_key, site in spec.get("sites", {}).items():
        devices.extend(site.get("devices", []))
    devices.extend(spec.get("wan_transport", {}).get("devices", []))
    for _zone_key, zone in spec.get("security", {}).items():
        devices.extend(zone.get("firewalls", []))
    return devices


def _collect_all_links(spec: dict) -> list[dict]:
    """Collect ALL links by scanning device interface peer fields.

    This is more complete than reading only the explicit links arrays,
    because some connections (FW-to-CE, host-to-leaf) are defined in
    device interfaces but not in the site/wan links arrays.

    Deduplicates: each A<->Z pair appears only once (not both directions).
    """
    seen: set[tuple[str, str, str, str]] = set()
    links: list[dict] = []

    all_devices = _collect_all_devices(spec)
    for dev in all_devices:
        dev_name = dev["name"]
        for iface in dev.get("interfaces", []):
            peer = iface.get("peer")
            peer_iface = iface.get("peer_interface")
            if not peer or not peer_iface:
                continue

            # Deduplicate: normalize to sorted pair so A->B and B->A are the same
            key = tuple(sorted([(dev_name, iface["name"]), (peer, peer_iface)]))
            if key in seen:
                continue
            seen.add(key)

            links.append(
                {
                    "a_end": {"device": dev_name, "interface": iface["name"]},
                    "z_end": {"device": peer, "interface": peer_iface},
                }
            )

    return links


def _eve_label(platform: str, iface_name: str) -> str:
    """Convert spec interface name to EVE-NG short label.

    EVE-NG uses short names: Ethernet1 -> Eth1, GigabitEthernet1 -> Gi1.
    FortiGate port1 and Linux eth0 stay as-is.
    """
    if platform == "arista_eos":
        return iface_name.replace("Ethernet", "Eth")
    if platform == "cisco_iosxe":
        return iface_name.replace("GigabitEthernet", "Gi")
    if platform == "linux":
        # EVE-NG Alpine linux template uses e0/e1 not eth0/eth1
        return iface_name.replace("eth", "e")
    return iface_name


def create_topology(
    spec: dict,
    lab_name: str = LAB_NAME,
    dry_run: bool = False,
    creds: object | None = None,
) -> None:
    """Create the full EVE-NG topology from the spec using evengsdk."""
    devices = _collect_all_devices(spec)
    links = _collect_all_links(spec)
    platform_by_name = {dev["name"]: dev["platform"] for dev in devices}

    # Initialize evengsdk
    eve = None
    if not dry_run and creds:
        from evengsdk.api import EvengApi
        from evengsdk.client import EvengClient as SdkClient

        sdk_client = SdkClient(
            creds.eve_ng_host,
            log_level="WARNING",
            ssl_verify=False,
            protocol="https",
        )
        sdk_client.login(username=creds.eve_ng_username, password=creds.eve_ng_password)
        eve = EvengApi(sdk_client)

    try:
        _run_topology_steps(eve, devices, links, platform_by_name, lab_name, dry_run)
    finally:
        if eve:
            eve.client.logout()


def _run_topology_steps(
    eve: object | None,
    devices: list[dict],
    links: list[dict],
    platform_by_name: dict[str, str],
    lab_name: str,
    dry_run: bool,
) -> None:
    """Execute the topology creation steps."""
    # EVE-NG Pro normalize_path prepends /admin/ unless path starts with /.
    # Our labs live at root, so prefix with / to get the correct API path.
    lab_path = f"/{lab_name}"

    # --- Step 1: Create the lab ---
    print(f"Creating lab: {lab_name}")
    if not dry_run and eve:
        try:
            eve.create_lab(lab_name, description="AI Infrastructure Lab — 21 nodes, 3 sites")
        except Exception as e:
            if "already exists" in str(e).lower() or "60028" in str(e):
                print("  Lab already exists, continuing...")
            else:
                raise

    # --- Step 2: Add management network (Cloud0 / pnet0) ---
    print("Adding management network (Cloud0/pnet0)")
    if not dry_run and eve:
        try:
            eve.add_lab_network(lab_path, network_type="pnet0", name="Management")
        except Exception:
            print("  Management network may already exist, continuing...")

    # --- Step 3: Add all 21 nodes ---
    print(f"\nAdding {len(devices)} nodes:")
    for i, dev in enumerate(devices, start=1):
        name = dev["name"]
        platform = dev["platform"]
        mapping = PLATFORM_MAP.get(platform)

        if not mapping:
            print(f"  SKIP {name}: no template mapping for platform '{platform}'")
            continue

        left, top = LAYOUT.get(name, (50 * i, 50 * i))
        print(f"  [{i:2d}] {name:<15s} ({mapping['template']}/{mapping['image']})")

        if not dry_run and eve:
            try:
                eve.add_node(
                    lab_path,
                    node_type="qemu",
                    template=mapping["template"],
                    image=mapping["image"],
                    name=name,
                    ram=mapping["ram"],
                    cpu=mapping["cpu"],
                    ethernet=mapping["ethernet"],
                    left=left,
                    top=top,
                )
            except Exception as e:
                print(f"    ERROR: {e}")

    # --- Step 4: Wire P2P links ---
    print(f"\nWiring {len(links)} P2P links:")
    wired = 0
    for i, link in enumerate(links, start=1):
        a_dev = link["a_end"]["device"]
        a_iface = link["a_end"]["interface"]
        z_dev = link["z_end"]["device"]
        z_iface = link["z_end"]["interface"]

        a_label = _eve_label(platform_by_name.get(a_dev, ""), a_iface)
        z_label = _eve_label(platform_by_name.get(z_dev, ""), z_iface)
        label = f"{a_dev}:{a_iface} <-> {z_dev}:{z_iface}"

        print(f"  [{i:2d}] {label}", end="")

        if not dry_run and eve:
            try:
                eve.connect_node_to_node(lab_path, a_dev, a_label, z_dev, z_label)
                print(" — OK")
                wired += 1
            except Exception as e:
                print(f" — ERROR: {e}")
        else:
            print(" — OK (dry run)")
            wired += 1

    # --- Step 5: Connect management interfaces to Cloud0 ---
    print("\nConnecting management interfaces to Cloud0:")
    mgmt_connected = 0
    for dev in devices:
        name = dev["name"]
        platform = dev["platform"]
        mgmt_label = MGMT_IFACE_LABEL.get(platform)

        if not mgmt_label:
            print(f"  {name:<15s} — SKIP (no mgmt label for {platform})")
            continue

        print(f"  {name:<15s} {mgmt_label}", end="")

        if not dry_run and eve:
            try:
                eve.connect_node_to_cloud(lab_path, name, mgmt_label, "Management")
                print(f" — OK ({MGMT_IPS.get(name, '')})")
                mgmt_connected += 1
            except Exception as e:
                print(f" — ERROR: {e}")
        else:
            print(f" — OK (dry run, {MGMT_IPS.get(name, '')})")
            mgmt_connected += 1

    # --- Summary ---
    print(f"\n{'DRY RUN — ' if dry_run else ''}Topology complete:")
    print(f"  Lab: {lab_name}")
    print(f"  Nodes: {len(devices)}")
    print(f"  P2P links wired: {wired}/{len(links)}")
    print(f"  Mgmt connections: {mgmt_connected}/{len(devices)}")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Create EVE-NG lab topology from spec")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH, help="Path to YAML spec")
    parser.add_argument("--lab-name", default=LAB_NAME, help="Lab name in EVE-NG")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec file not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())

    if args.dry_run:
        print("=== DRY RUN MODE — no changes will be made ===\n")
        create_topology(spec, args.lab_name, dry_run=True)
        return

    creds = require_credentials("eve_ng_host", "eve_ng_password")
    print(f"Connecting to EVE-NG at {creds.eve_ng_host}...")
    create_topology(spec, args.lab_name, creds=creds)


if __name__ == "__main__":
    main()
