"""Enrich NetBox with all data-plane facts derivable from the spec.

Idempotent. For each lab device: ensures mgmt interface + IP, sets primary_ip4,
populates bgp.asn in local_context_data, syncs serial number, removes orphan
interfaces no longer in spec, and creates lab prefixes. Touches only lab
devices (those listed in lab_spec.yaml + lab_bootstrap.yaml).

Usage:
    python -m scripts.netbox_enrich
    python -m scripts.netbox_enrich --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pynetbox
import yaml

from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
BOOTSTRAP_PATH = Path(__file__).parent.parent / "configs" / "lab_bootstrap.yaml"

PLATFORM_MGMT_IFACE = {
    "arista_eos": "Mgmt1",
    "cisco_iosxe": "GigabitEthernet5",
    "fortinet_fortios": "port6",
    "linux": "e1",
}


def all_lab_devices(spec: dict) -> list[dict]:
    """Yield every device in the spec, regardless of section."""
    out = []
    for site in spec.get("sites", {}).values():
        out.extend(site.get("devices", []))
    out.extend(spec.get("wan_transport", {}).get("devices", []))
    for sec in spec.get("security", {}).values():
        out.extend(sec.get("firewalls", []))
    return out


def get_device_asn(spec: dict, name: str) -> int | None:
    for d in all_lab_devices(spec):
        if d["name"] == name:
            return d.get("asn")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich NetBox with lab facts")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    spec = yaml.safe_load(SPEC_PATH.read_text())
    bootstrap = yaml.safe_load(BOOTSTRAP_PATH.read_text())
    mgmt_ips = bootstrap["management"]["devices"]
    mgmt_gateway = bootstrap["management"]["gateway"]

    creds = require_credentials("netbox_url", "netbox_token")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)

    print(f"Connected: {creds.netbox_url}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'WRITE'}\n")

    lab_devices = all_lab_devices(spec)
    lab_device_names = {d["name"] for d in lab_devices}

    summary = {
        "mgmt_iface_created": 0,
        "mgmt_ip_assigned": 0,
        "primary_ip_set": 0,
        "bgp_asn_set": 0,
        "serial_set": 0,
        "orphan_iface_deleted": 0,
        "prefix_created": 0,
    }

    # 1. Per-device enrichment
    for dev in lab_devices:
        name = dev["name"]
        platform = dev["platform"]
        nb_dev = nb.dcim.devices.get(name=name)
        if not nb_dev:
            print(f"  {name}: SKIP (not in NetBox)")
            continue

        # 1a. Management interface
        mgmt_iface_name = PLATFORM_MGMT_IFACE.get(platform)
        mgmt_ip = mgmt_ips.get(name)
        mgmt_iface = None
        if mgmt_iface_name and mgmt_ip:
            mgmt_iface = nb.dcim.interfaces.get(device_id=nb_dev.id, name=mgmt_iface_name)
            if not mgmt_iface:
                if not args.dry_run:
                    mgmt_iface = nb.dcim.interfaces.create(
                        {
                            "device": nb_dev.id,
                            "name": mgmt_iface_name,
                            "type": "1000base-t",
                            "description": "Out-of-band management",
                            "mgmt_only": True,
                        }
                    )
                summary["mgmt_iface_created"] += 1
                print(f"  {name}: created mgmt iface {mgmt_iface_name}")

        # 1b. Management IP — must be unique per address. Search globally first.
        mgmt_ip_addr = f"{mgmt_ip}/22" if mgmt_ip else None
        nb_mgmt_ip = None
        if mgmt_ip_addr:
            existing = list(nb.ipam.ip_addresses.filter(address=mgmt_ip_addr))
            if existing:
                nb_mgmt_ip = existing[0]
                # Ensure it's attached to the right interface
                if mgmt_iface and (
                    nb_mgmt_ip.assigned_object_type != "dcim.interface"
                    or nb_mgmt_ip.assigned_object_id != mgmt_iface.id
                ):
                    if not args.dry_run:
                        nb_mgmt_ip.assigned_object_type = "dcim.interface"
                        nb_mgmt_ip.assigned_object_id = mgmt_iface.id
                        nb_mgmt_ip.save()
                    summary["mgmt_ip_assigned"] += 1
                    print(f"  {name}: re-assigned {mgmt_ip_addr} to {mgmt_iface_name}")
            elif mgmt_iface:
                if not args.dry_run:
                    nb_mgmt_ip = nb.ipam.ip_addresses.create(
                        {
                            "address": mgmt_ip_addr,
                            "assigned_object_type": "dcim.interface",
                            "assigned_object_id": mgmt_iface.id,
                            "description": f"Management {name}",
                        }
                    )
                summary["mgmt_ip_assigned"] += 1
                print(f"  {name}: created mgmt IP {mgmt_ip_addr}")

        # 1c. Set primary_ip4
        if nb_mgmt_ip and (not nb_dev.primary_ip4 or nb_dev.primary_ip4.id != nb_mgmt_ip.id):
            if not args.dry_run:
                nb_dev.primary_ip4 = nb_mgmt_ip.id
                nb_dev.save()
            summary["primary_ip_set"] += 1
            print(f"  {name}: primary_ip4 → {mgmt_ip_addr}")

        # 1d. local_context_data: bgp.asn + role hint
        ctx = nb_dev.local_context_data or {}
        asn = get_device_asn(spec, name)
        changed = False
        if asn:
            if ctx.get("bgp", {}).get("asn") != asn:
                ctx.setdefault("bgp", {})["asn"] = asn
                changed = True
        spec_role = dev.get("role", "")
        if spec_role and ctx.get("role") != spec_role:
            ctx["role"] = spec_role
            changed = True
        if changed:
            if not args.dry_run:
                nb_dev.local_context_data = ctx
                nb_dev.save()
            summary["bgp_asn_set"] += 1
            print(f"  {name}: ctx bgp.asn={asn} role={spec_role}")

        # 1e. Serial number — synthetic for VMs
        synthetic_serial = f"VM-{name.upper()}"
        if (nb_dev.serial or "") != synthetic_serial:
            if not args.dry_run:
                nb_dev.serial = synthetic_serial
                nb_dev.save()
            summary["serial_set"] += 1

        # 1f. Orphan interface cleanup — delete any interface in NetBox not in spec
        spec_iface_names = {i["name"] for i in dev.get("interfaces", [])}
        if "loopback0" in dev:
            spec_iface_names.add("Loopback0")
        if mgmt_iface_name:
            spec_iface_names.add(mgmt_iface_name)

        nb_ifaces = list(nb.dcim.interfaces.filter(device_id=nb_dev.id))
        for ifc in nb_ifaces:
            if ifc.name not in spec_iface_names:
                # Check if it has IPs/cables before deleting
                ips_on = list(nb.ipam.ip_addresses.filter(interface_id=ifc.id))
                for ip in ips_on:
                    if not args.dry_run:
                        ip.delete()
                if not args.dry_run:
                    try:
                        ifc.delete()
                    except Exception as e:
                        print(f"  {name}: could not delete {ifc.name}: {e}")
                        continue
                summary["orphan_iface_deleted"] += 1
                print(f"  {name}: removed orphan iface {ifc.name}")

    # 2. Lab prefixes — derive from spec subnets
    print("\n=== Prefixes ===")
    lab_prefixes = [
        ("10.1.0.0/24", "DC underlay loopbacks"),
        ("10.1.1.0/24", "DC underlay P2P"),
        ("10.1.2.0/24", "DC VTEP loopbacks"),
        ("10.10.1.0/24", "DC overlay VNI 10100 (SERVERS_A)"),
        ("10.10.2.0/24", "DC overlay VNI 10200 (SERVERS_B)"),
        ("10.20.0.0/16", "Branch site"),
        ("10.30.0.0/16", "DR overlay"),
        ("10.31.0.0/24", "DR underlay loopbacks"),
        ("10.31.1.0/24", "DR underlay P2P"),
        ("10.31.2.0/24", "DR VTEP loopbacks"),
        ("10.99.0.0/24", "Border-to-FW transit"),
        ("10.99.1.0/24", "FW-to-CE transit"),
        ("172.16.0.0/24", "SP transport"),
        ("172.16.0.0/16", "SP/CE loopback range"),
        ("192.168.68.0/22", "Out-of-band management"),
    ]
    for cidr, desc in lab_prefixes:
        existing = nb.ipam.prefixes.get(prefix=cidr)
        if existing:
            if existing.description != desc:
                if not args.dry_run:
                    existing.description = desc
                    existing.save()
                print(f"  {cidr}: updated description")
            continue
        if not args.dry_run:
            nb.ipam.prefixes.create(
                {"prefix": cidr, "description": desc, "status": "active"}
            )
        summary["prefix_created"] += 1
        print(f"  {cidr}: created — {desc}")

    print("\n=== Summary ===")
    print(f"  Lab devices touched: {len(lab_device_names)}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"  Mgmt gateway: {mgmt_gateway}")


if __name__ == "__main__":
    sys.exit(main())
