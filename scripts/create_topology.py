"""Create the 21-node EVE-NG lab topology from the YAML spec.

Reads the spec for device names, roles, and platforms, then uses the
EVE-NG REST API to create the lab, add nodes, add networks, and wire
interfaces end-to-end.

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
from scripts.eve_client import EveNgClient

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
LAB_NAME = "AI-Infra-Lab"

# EVE-NG template + image mapping per platform.
# Adjust image names to match what's installed on your EVE-NG server.
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

# EVE-NG interface name → integer index mapping per template.
# QEMU nodes use sequential integer IDs starting at 0.
# Management interface is always index 0 for network devices.
IFACE_INDEX: dict[str, dict[str, int]] = {
    "arista_eos": {
        "Management1": 0,
        "Ethernet1": 1,
        "Ethernet2": 2,
        "Ethernet3": 3,
        "Ethernet4": 4,
        "Ethernet5": 5,
        "Ethernet6": 6,
        "Ethernet7": 7,
    },
    "cisco_iosxe": {
        # C8000v: 5 interfaces total. Gi1-Gi4 = data, Gi5 = management.
        # EVE-NG index 0 maps to GigabitEthernet1 inside IOS-XE, etc.
        "GigabitEthernet1": 0,
        "GigabitEthernet2": 1,
        "GigabitEthernet3": 2,
        "GigabitEthernet4": 3,
        "GigabitEthernet5": 4,  # Management
    },
    "fortinet_fortios": {
        "port1": 0,
        "port2": 1,
        "port3": 2,
        "port4": 3,
        "port5": 4,
        "port6": 5,
    },
    "linux": {
        "eth0": 0,
        "eth1": 1,
    },
}

# Which interface index is the management interface per platform.
MGMT_IFACE_INDEX: dict[str, int] = {
    "arista_eos": 0,  # Management1 (dedicated, separate from Ethernet1+)
    "cisco_iosxe": 4,  # GigabitEthernet5 (last of 5 interfaces, keeps Gi1-4 for data)
    "fortinet_fortios": 5,  # port6 (last port, keeps port1-5 for data)
    "linux": 1,  # eth1 (keeps eth0 for data)
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
    """Collect all links from every section of the spec."""
    links = []
    for _site_key, site in spec.get("sites", {}).items():
        links.extend(site.get("links", []))
    links.extend(spec.get("wan_transport", {}).get("links", []))
    return links


def _resolve_iface_index(platform: str, iface_name: str) -> int | None:
    """Map an interface name to its EVE-NG integer index.

    Returns None if the interface isn't in the mapping (unmapped or unknown).
    """
    platform_map = IFACE_INDEX.get(platform, {})
    return platform_map.get(iface_name)


def _build_device_platform_map(devices: list[dict]) -> dict[str, str]:
    """Build device_name -> platform lookup from the device list."""
    return {dev["name"]: dev["platform"] for dev in devices}


def create_topology(
    client: EveNgClient,
    spec: dict,
    lab_name: str = LAB_NAME,
    dry_run: bool = False,
) -> None:
    """Create the full EVE-NG topology from the spec.

    Steps:
        1. Create the lab
        2. Add management network (Cloud0 bridged to pnet0)
        3. Add all 21 nodes
        4. Create a bridge per P2P link and wire both endpoints to it
        5. Connect every node's management interface to the management network
    """
    lab_path = f"/{lab_name}.unl"
    devices = _collect_all_devices(spec)
    links = _collect_all_links(spec)
    platform_by_name = _build_device_platform_map(devices)

    # --- Step 1: Create the lab ---
    print(f"Creating lab: {lab_name}")
    if not dry_run:
        try:
            client.create_lab(lab_name, description="AI Infrastructure Lab — 21 nodes, 3 sites")
        except Exception as e:
            if "already exists" in str(e).lower() or "409" in str(e):
                print("  Lab already exists, continuing...")
            else:
                raise

    # --- Step 2: Add management network (Cloud0 bridged to pnet0) ---
    print("Adding management network (Cloud0/pnet0)")
    mgmt_net_id: int | None = None
    if not dry_run:
        try:
            result = client.add_network(lab_path, name="Management", net_type="pnet0")
            mgmt_net_id = result.get("id") if isinstance(result, dict) else None
        except Exception:
            print("  Management network may already exist, fetching ID...")
            try:
                networks = client.get_networks(lab_path)
                for net_id, net in networks.items():
                    if net.get("type") == "pnet0" or net.get("name") == "Management":
                        mgmt_net_id = int(net_id)
                        break
            except Exception:
                pass
        if mgmt_net_id:
            print(f"  Management network ID: {mgmt_net_id}")
        else:
            print("  WARNING: Could not determine management network ID")
    else:
        mgmt_net_id = 1

    # --- Step 3: Add all 21 nodes ---
    node_ids: dict[str, int] = {}
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

        if not dry_run:
            try:
                result = client.add_node(
                    lab_path,
                    name=name,
                    template=mapping["template"],
                    image=mapping["image"],
                    ram=mapping["ram"],
                    cpu=mapping["cpu"],
                    ethernet=mapping["ethernet"],
                    left=left,
                    top=top,
                )
                # EVE-NG returns the node ID in the response
                node_id = result.get("id")
                if node_id is None:
                    print(f"    WARNING: No ID in response, using index {i}")
                    node_id = i
                node_ids[name] = int(node_id)
            except Exception as e:
                print(f"    ERROR: {e}")
        else:
            node_ids[name] = i

    if not dry_run:
        created = len(node_ids)
        expected = len([d for d in devices if d["platform"] in PLATFORM_MAP])
        if created < expected:
            print(f"\n  WARNING: Only {created}/{expected} nodes created. Fix errors above.")

    # --- Step 4: Wire P2P links ---
    # Each link gets its own bridge. Both endpoints connect to that bridge.
    print(f"\nWiring {len(links)} P2P links:")
    wired = 0
    for i, link in enumerate(links, start=1):
        a_dev = link["a_end"]["device"]
        a_iface = link["a_end"]["interface"]
        z_dev = link["z_end"]["device"]
        z_iface = link["z_end"]["interface"]

        a_platform = platform_by_name.get(a_dev, "")
        z_platform = platform_by_name.get(z_dev, "")
        a_idx = _resolve_iface_index(a_platform, a_iface)
        z_idx = _resolve_iface_index(z_platform, z_iface)

        status = ""
        if a_idx is None:
            status = f"SKIP (unknown interface {a_iface} for {a_platform})"
        elif z_idx is None:
            status = f"SKIP (unknown interface {z_iface} for {z_platform})"
        elif a_dev not in node_ids:
            status = f"SKIP (node {a_dev} not created)"
        elif z_dev not in node_ids:
            status = f"SKIP (node {z_dev} not created)"

        if status:
            print(f"  [{i:2d}] {a_dev}:{a_iface} <-> {z_dev}:{z_iface} — {status}")
            continue

        print(f"  [{i:2d}] {a_dev}:{a_iface}(i{a_idx}) <-> {z_dev}:{z_iface}(i{z_idx})", end="")

        if not dry_run:
            try:
                # Create bridge for this link
                net_name = f"link_{a_dev}_{a_iface}--{z_dev}_{z_iface}"[:64]
                net_result = client.add_network(lab_path, name=net_name, net_type="bridge")
                net_id = net_result.get("id") if isinstance(net_result, dict) else None

                if net_id is None:
                    print(" — ERROR: no network ID returned")
                    continue

                net_id = int(net_id)

                # Connect A-end to bridge
                client.connect_node_to_network(lab_path, node_ids[a_dev], a_idx, net_id)
                # Connect Z-end to bridge
                client.connect_node_to_network(lab_path, node_ids[z_dev], z_idx, net_id)
                print(f" — OK (net {net_id})")
                wired += 1
            except Exception as e:
                print(f" — ERROR: {e}")
        else:
            print(" — OK (dry run)")
            wired += 1

    # --- Step 5: Connect management interfaces to pnet0 ---
    print(f"\nConnecting management interfaces to Cloud0 (net {mgmt_net_id}):")
    mgmt_connected = 0
    for name, node_id in sorted(node_ids.items()):
        platform = platform_by_name.get(name, "")
        mgmt_idx = MGMT_IFACE_INDEX.get(platform)

        if mgmt_idx is None:
            print(f"  {name:<15s} — SKIP (no mgmt interface mapping for {platform})")
            continue

        print(f"  {name:<15s} interface index {mgmt_idx}", end="")

        if not dry_run and mgmt_net_id is not None:
            try:
                client.connect_node_to_network(lab_path, node_id, mgmt_idx, mgmt_net_id)
                print(f" — OK ({MGMT_IPS.get(name, 'no IP assigned')})")
                mgmt_connected += 1
            except Exception as e:
                print(f" — ERROR: {e}")
        else:
            print(f" — OK (dry run, {MGMT_IPS.get(name, '')})")
            mgmt_connected += 1

    # --- Summary ---
    print(f"\n{'DRY RUN — ' if dry_run else ''}Topology complete:")
    print(f"  Lab: {lab_name}")
    print(f"  Nodes created: {len(node_ids)}")
    print(f"  P2P links wired: {wired}/{len(links)}")
    print(f"  Mgmt connections: {mgmt_connected}/{len(node_ids)}")


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
        create_topology(EveNgClient("", "", ""), spec, args.lab_name, dry_run=True)
        return

    creds = require_credentials("eve_ng_host", "eve_ng_password")
    client = EveNgClient(creds.eve_ng_host, creds.eve_ng_username, creds.eve_ng_password)

    print(f"Connecting to EVE-NG at {creds.eve_ng_host}...")
    client.login()
    print("Authenticated.\n")

    try:
        create_topology(client, spec, args.lab_name)
    finally:
        client.logout()


if __name__ == "__main__":
    main()
