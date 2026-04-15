"""Populate NetBox with the full 21-node AI Infrastructure Lab data model.

Creates sites, manufacturers, platforms, device types, device roles,
devices, interfaces, IP addresses, prefixes, cables, and config contexts
to match the YAML spec. Idempotent — safe to run multiple times.

Usage:
    python -m scripts.populate_netbox
    python -m scripts.populate_netbox --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"


def _get_or_create(endpoint, search: dict, create_data: dict, label: str = ""):
    """Get existing object or create it. Returns the object."""
    existing = endpoint.get(**search)
    if existing:
        return existing
    result = endpoint.create(create_data)
    print(f"  Created {label}: {create_data.get('name', create_data.get('model', ''))}")
    return result


def populate(nb, spec: dict, dry_run: bool = False) -> None:
    """Populate NetBox from the YAML spec."""

    # ------------------------------------------------------------------
    # 1. Manufacturers
    # ------------------------------------------------------------------
    print("Step 1: Manufacturers")
    manufacturers = {}
    for name, slug in [
        ("Arista", "arista"),
        ("Cisco", "cisco"),
        ("Fortinet", "fortinet"),
        ("Generic", "generic"),
    ]:
        if dry_run:
            print(f"  Would create manufacturer: {name}")
            continue
        manufacturers[slug] = _get_or_create(
            nb.dcim.manufacturers,
            {"slug": slug},
            {"name": name, "slug": slug},
            "manufacturer",
        )

    # ------------------------------------------------------------------
    # 2. Platforms
    # ------------------------------------------------------------------
    print("Step 2: Platforms")
    platform_defs = [
        ("Arista EOS", "arista_eos", "arista"),
        ("Cisco IOS-XE", "cisco_iosxe", "cisco"),
        ("Fortinet FortiOS", "fortinet_fortios", "fortinet"),
        ("Linux", "linux", "generic"),
    ]
    platforms = {}
    for name, slug, mfr_slug in platform_defs:
        if dry_run:
            print(f"  Would create platform: {name}")
            continue
        mfr = manufacturers.get(mfr_slug)
        mfr_id = mfr.id if mfr else None
        platforms[slug] = _get_or_create(
            nb.dcim.platforms,
            {"slug": slug},
            {"name": name, "slug": slug, "manufacturer": mfr_id},
            "platform",
        )

    # ------------------------------------------------------------------
    # 3. Device Types
    # ------------------------------------------------------------------
    print("Step 3: Device Types")
    device_type_defs = [
        ("vEOS", "veos", "arista"),
        ("C8000v", "c8000v", "cisco"),
        ("FortiGate-VM", "fortigate-vm", "fortinet"),
        ("Alpine Linux", "alpine-linux", "generic"),
    ]
    device_types = {}
    for model, slug, mfr_slug in device_type_defs:
        if dry_run:
            print(f"  Would create device type: {model}")
            continue
        mfr = manufacturers.get(mfr_slug)
        mfr_id = mfr.id if mfr else None
        device_types[slug] = _get_or_create(
            nb.dcim.device_types,
            {"slug": slug},
            {"model": model, "slug": slug, "manufacturer": mfr_id},
            "device type",
        )

    # ------------------------------------------------------------------
    # 4. Device Roles
    # ------------------------------------------------------------------
    print("Step 4: Device Roles")
    role_defs = [
        ("Spine", "spine", "4caf50"),
        ("Leaf", "leaf", "8bc34a"),
        ("Border Leaf", "border-leaf", "ff9800"),
        ("CE Router", "ce", "2196f3"),
        ("PE Router", "pe", "9c27b0"),
        ("Firewall", "firewall", "f44336"),
        ("Host", "host", "607d8b"),
    ]
    roles = {}
    for name, slug, color in role_defs:
        if dry_run:
            print(f"  Would create role: {name}")
            continue
        roles[slug] = _get_or_create(
            nb.dcim.device_roles,
            {"slug": slug},
            {"name": name, "slug": slug, "color": color},
            "role",
        )

    # ------------------------------------------------------------------
    # 5. Sites
    # ------------------------------------------------------------------
    print("Step 5: Sites")
    site_defs = [
        ("DC-East", "dc-east"),
        ("Branch-01", "branch-01"),
        ("DR-West", "dr-west"),
    ]
    sites = {}
    for name, slug in site_defs:
        if dry_run:
            print(f"  Would create site: {name}")
            continue
        sites[slug] = _get_or_create(
            nb.dcim.sites,
            {"slug": slug},
            {"name": name, "slug": slug, "status": "active"},
            "site",
        )

    if dry_run:
        print("\nDry run complete — no changes made.")
        return

    # ------------------------------------------------------------------
    # 6. Devices + Interfaces + IPs
    # ------------------------------------------------------------------
    print("Step 6: Devices, Interfaces, and IPs")
    skipped_ips: list[str] = []  # Track IPs that couldn't be created (duplicates)

    # Map platform slug to device type and site assignment
    platform_to_dtype = {
        "arista_eos": "veos",
        "cisco_iosxe": "c8000v",
        "fortinet_fortios": "fortigate-vm",
        "linux": "alpine-linux",
    }

    # Site slug mapping from spec keys
    spec_to_site = {
        "dc_east": "dc-east",
        "branch_01": "branch-01",
        "dr_west": "dr-west",
    }

    # Collect all devices with their site assignment
    all_devices_with_site = []
    for spec_key, site_slug in spec_to_site.items():
        site_data = spec.get("sites", {}).get(spec_key, {})
        for dev in site_data.get("devices", []):
            all_devices_with_site.append((dev, site_slug))

    # WAN transport devices go to dc-east (physically located there)
    for dev in spec.get("wan_transport", {}).get("devices", []):
        all_devices_with_site.append((dev, "dc-east"))

    # Security devices — DC firewalls to dc-east, DR firewalls to dr-west
    for zone_key, zone in spec.get("security", {}).items():
        site_slug = "dr-west" if zone_key == "dr" else "dc-east"
        for dev in zone.get("firewalls", []):
            all_devices_with_site.append((dev, site_slug))

    # Agent boundary from spec
    agent = spec.get("agent", {}).get("boundary", {})
    managed_devices = set(agent.get("managed", []))
    excluded_devices = set(agent.get("excluded", []))

    created_devices = {}
    for dev, site_slug in all_devices_with_site:
        dev_name = dev["name"]
        platform_slug = dev["platform"]
        role_slug = dev["role"]
        dtype_slug = platform_to_dtype.get(platform_slug, "")

        site = sites.get(site_slug)
        role = roles.get(role_slug)
        dtype = device_types.get(dtype_slug)
        platform = platforms.get(platform_slug)

        if not all([site, role, dtype]):
            print(f"  SKIP {dev_name}: missing site/role/dtype")
            continue

        # Create device
        existing = nb.dcim.devices.get(name=dev_name)
        if existing:
            nb_dev = existing
        else:
            nb_dev = nb.dcim.devices.create(
                {
                    "name": dev_name,
                    "device_type": dtype.id,
                    "role": role.id,
                    "site": site.id,
                    "platform": platform.id if platform else None,
                    "status": "active",
                }
            )
            print(f"  Created device: {dev_name}")

        created_devices[dev_name] = nb_dev

        # Create interfaces
        for iface_def in dev.get("interfaces", []):
            iface_name = iface_def["name"]
            existing_iface = nb.dcim.interfaces.get(device=dev_name, name=iface_name)
            if not existing_iface:
                iface = nb.dcim.interfaces.create(
                    {
                        "device": nb_dev.id,
                        "name": iface_name,
                        "type": "virtual" if iface_name.startswith("Loopback") else "1000base-t",
                        "description": iface_def.get("description", ""),
                    }
                )
            else:
                iface = existing_iface

            # Assign IP if present
            if "ipv4" in iface_def:
                ip_addr = iface_def["ipv4"]
                try:
                    nb.ipam.ip_addresses.create(
                        {
                            "address": ip_addr,
                            "assigned_object_type": "dcim.interface",
                            "assigned_object_id": iface.id,
                        }
                    )
                except Exception as e:
                    err = str(e)
                    if "Duplicate" in err:
                        skipped_ips.append(f"{dev_name}:{iface_name} = {ip_addr}")
                    else:
                        print(f"    IP ERROR {dev_name}:{iface_name} {ip_addr}: {err}")

        # Create Loopback0 if device has loopback0 field
        if "loopback0" in dev:
            lo_name = "Loopback0"
            existing_lo = nb.dcim.interfaces.get(device=dev_name, name=lo_name)
            if not existing_lo:
                lo_iface = nb.dcim.interfaces.create(
                    {
                        "device": nb_dev.id,
                        "name": lo_name,
                        "type": "virtual",
                        "description": "Router ID",
                    }
                )
            else:
                lo_iface = existing_lo

            lo_ip = dev["loopback0"]
            try:
                nb.ipam.ip_addresses.create(
                    {
                        "address": lo_ip,
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": lo_iface.id,
                    }
                )
            except Exception as e:
                err = str(e)
                if "Duplicate" in err:
                    skipped_ips.append(f"{dev_name}:Loopback0 = {lo_ip}")
                else:
                    print(f"    IP ERROR {dev_name}:Loopback0 {lo_ip}: {err}")

        # Set config context for agent boundary
        if dev_name in managed_devices:
            boundary = "managed"
        elif dev_name in excluded_devices:
            boundary = "excluded"
        else:
            boundary = None

        # Build config context data
        config_ctx = {}
        if boundary:
            config_ctx["agent_boundary"] = boundary
        if "asn" in dev:
            config_ctx["bgp_config"] = {"asn": dev["asn"]}

        if config_ctx:
            nb_dev.local_context_data = config_ctx
            nb_dev.save()

    # ------------------------------------------------------------------
    # 7. Prefixes
    # ------------------------------------------------------------------
    print("Step 7: Prefixes")
    prefix_defs = [
        ("10.1.0.0/24", "DC underlay loopbacks", "dc-east"),
        ("10.1.1.0/24", "DC underlay P2P", "dc-east"),
        ("10.1.2.0/24", "DC VTEP loopbacks", "dc-east"),
        ("10.10.1.0/24", "DC overlay VNI 10100", "dc-east"),
        ("10.10.2.0/24", "DC overlay VNI 10200", "dc-east"),
        ("10.99.0.0/24", "Border-to-FW transit", "dc-east"),
        ("10.99.1.0/24", "FW-to-CE transit", "dc-east"),
        ("172.16.0.0/24", "SP transport", None),
        ("10.20.0.0/16", "Branch addressing", "branch-01"),
        ("10.30.0.0/16", "DR overlay", "dr-west"),
        ("10.31.0.0/24", "DR underlay", "dr-west"),
    ]
    for prefix, desc, site_slug in prefix_defs:
        existing = nb.ipam.prefixes.get(prefix=prefix)
        if not existing:
            data = {
                "prefix": prefix,
                "description": desc,
                "status": "active",
            }
            if site_slug and site_slug in sites:
                data["site"] = sites[site_slug].id
            nb.ipam.prefixes.create(data)
            print(f"  Created prefix: {prefix} ({desc})")

    # ------------------------------------------------------------------
    # 8. Cables
    # ------------------------------------------------------------------
    print("Step 8: Cables")
    cable_count = 0

    # Collect all links from device interface peer fields
    seen_cables = set()
    for dev, _site_slug in all_devices_with_site:
        dev_name = dev["name"]
        for iface_def in dev.get("interfaces", []):
            peer = iface_def.get("peer")
            peer_iface = iface_def.get("peer_interface")
            if not peer or not peer_iface:
                continue

            # Deduplicate
            cable_key = tuple(sorted([(dev_name, iface_def["name"]), (peer, peer_iface)]))
            if cable_key in seen_cables:
                continue
            seen_cables.add(cable_key)

            # Look up interface objects
            a_iface = nb.dcim.interfaces.get(device=dev_name, name=iface_def["name"])
            z_iface = nb.dcim.interfaces.get(device=peer, name=peer_iface)

            if not a_iface or not z_iface:
                a_end = f"{dev_name}:{iface_def['name']}"
                z_end = f"{peer}:{peer_iface}"
                print(f"  SKIP cable {a_end} <-> {z_end} (iface not found)")
                continue

            # Check if cable already exists
            existing_cables = nb.dcim.cables.filter(device=dev_name)
            already_cabled = False
            for c in existing_cables:
                terms = list(c.a_terminations) + list(c.b_terminations)
                term_ids = {t.get("object_id") for t in terms if isinstance(t, dict)}
                if a_iface.id in term_ids or z_iface.id in term_ids:
                    already_cabled = True
                    break

            if already_cabled:
                continue

            try:
                nb.dcim.cables.create(
                    {
                        "a_terminations": [
                            {"object_type": "dcim.interface", "object_id": a_iface.id}
                        ],
                        "b_terminations": [
                            {"object_type": "dcim.interface", "object_id": z_iface.id}
                        ],
                        "status": "connected",
                    }
                )
                cable_count += 1
            except Exception as e:
                print(f"  Cable {dev_name}:{iface_def['name']} <-> {peer}:{peer_iface}: {e}")

    print(f"  Created {cable_count} cables")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\nNetBox population complete:")
    print(f"  Sites: {len(sites)}")
    print(f"  Devices: {len(created_devices)}")
    print(f"  Prefixes: {len(prefix_defs)}")
    print(f"  Cables: {cable_count}")
    if skipped_ips:
        print(f"\n  WARNING: {len(skipped_ips)} IPs skipped (duplicate in NetBox):")
        for skip in skipped_ips:
            print(f"    {skip}")
        print("  These interfaces will have no IP in the generated spec.")
        print("  Fix: adjust addressing or reassign conflicting IPs in NetBox.")


def main() -> None:
    """CLI entrypoint."""
    import pynetbox

    parser = argparse.ArgumentParser(description="Populate NetBox with lab data from spec")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH, help="Path to YAML spec")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec file not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())
    creds = require_credentials("netbox_url", "netbox_token")

    print(f"Connecting to NetBox at {creds.netbox_url}...")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)
    print("Connected.\n")

    populate(nb, spec, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
