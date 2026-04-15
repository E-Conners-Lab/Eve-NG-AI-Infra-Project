"""Populate NetBox with full enterprise data model.

Adds organizational structure, racks, VLANs, VRFs, ASNs, circuits,
and contacts to make NetBox a complete enterprise source of truth.

Run AFTER populate_netbox.py (which creates devices, interfaces, IPs, cables).

Usage:
    python -m scripts.populate_netbox_enterprise
    python -m scripts.populate_netbox_enterprise --dry-run
"""

from __future__ import annotations

import argparse
import contextlib

import pynetbox
from pynetbox.core.query import RequestError

from scripts.credentials import require_credentials


def _get_or_create(endpoint, search: dict, create_data: dict, label: str = ""):
    """Get existing object or create it. Returns the object."""
    existing = endpoint.get(**search)
    if existing:
        return existing
    result = endpoint.create(create_data)
    print(f"    Created {label}: {create_data.get('name', create_data.get('display', ''))}")
    return result


def populate_enterprise(nb: pynetbox.api, creds: object | None = None) -> None:
    """Add enterprise organizational structure to NetBox."""

    # ==================================================================
    # 1. ORGANIZATION — Tenant Group + Tenant
    # ==================================================================
    print("1. Organization (Tenancy)")

    tenant_group = _get_or_create(
        nb.tenancy.tenant_groups,
        {"slug": "e-conners-lab"},
        {
            "name": "E-Conners Lab",
            "slug": "e-conners-lab",
            "description": "Home lab infrastructure organization",
        },
        "tenant group",
    )

    tenant = _get_or_create(
        nb.tenancy.tenants,
        {"slug": "ai-infra-lab"},
        {
            "name": "AI Infrastructure Lab",
            "slug": "ai-infra-lab",
            "group": tenant_group.id,
            "description": "Multi-site, multi-vendor network lab managed by AI agent",
        },
        "tenant",
    )

    # ==================================================================
    # 2. REGIONS — Geographic hierarchy
    # ==================================================================
    print("2. Regions")

    region_na = _get_or_create(
        nb.dcim.regions,
        {"slug": "north-america"},
        {"name": "North America", "slug": "north-america"},
        "region",
    )

    region_us_east = _get_or_create(
        nb.dcim.regions,
        {"slug": "us-east"},
        {
            "name": "US-East",
            "slug": "us-east",
            "parent": region_na.id,
            "description": "Primary datacenter region",
        },
        "region",
    )

    region_us_west = None
    existing_west = nb.dcim.regions.get(slug="us-west")
    if existing_west:
        region_us_west = existing_west
    else:
        region_us_west = _get_or_create(
            nb.dcim.regions,
            {"slug": "us-west"},
            {
                "name": "US-West",
                "slug": "us-west",
                "parent": region_na.id,
                "description": "DR recovery region",
            },
            "region",
        )

    region_us_central = _get_or_create(
        nb.dcim.regions,
        {"slug": "us-central"},
        {
            "name": "US-Central",
            "slug": "us-central",
            "parent": region_na.id,
            "description": "Branch office region",
        },
        "region",
    )

    # ==================================================================
    # 3. SITE GROUP — All sites under one umbrella
    # ==================================================================
    print("3. Site Group")

    site_group = _get_or_create(
        nb.dcim.site_groups,
        {"slug": "ai-infra-lab"},
        {
            "name": "AI Infrastructure Lab",
            "slug": "ai-infra-lab",
            "description": "All sites in the AI-managed network lab",
        },
        "site group",
    )

    # ==================================================================
    # 4. UPDATE SITES — Assign tenant, region, site group
    # ==================================================================
    print("4. Update Sites")

    site_assignments = {
        "dc-east": {
            "region": region_us_east.id,
            "group": site_group.id,
            "tenant": tenant.id,
            "facility": "Proxmox R640 — EVE-NG",
            "description": "Primary datacenter — EVPN-VXLAN fabric with HA firewalls",
            "physical_address": "Home Lab — Dell PowerEdge R640",
        },
        "branch-01": {
            "region": region_us_central.id,
            "group": site_group.id,
            "tenant": tenant.id,
            "facility": "Proxmox R640 — EVE-NG",
            "description": "Branch office — dual-homed CE to SP transport",
            "physical_address": "Home Lab — Dell PowerEdge R640",
        },
        "dr-west": {
            "region": region_us_west.id if region_us_west else None,
            "group": site_group.id,
            "tenant": tenant.id,
            "facility": "Proxmox R640 — EVE-NG",
            "description": "DR recovery site — collapsed EVPN with HA firewalls",
            "physical_address": "Home Lab — Dell PowerEdge R640",
        },
    }

    for slug, updates in site_assignments.items():
        site = nb.dcim.sites.get(slug=slug)
        if site:
            for key, val in updates.items():
                if val is not None:
                    setattr(site, key, val)
            site.save()
            print(f"    Updated site: {site.name}")

    # ==================================================================
    # 5. RACKS — Per-site rack layouts
    # ==================================================================
    print("5. Racks")

    rack_defs = [
        {
            "name": "DC-East-R1",
            "site_slug": "dc-east",
            "u_height": 42,
            "desc": "DC-East primary rack — spine/leaf fabric",
            "devices": {
                "dc-spine-1": 40,
                "dc-spine-2": 38,
                "dc-leaf-1": 34,
                "dc-leaf-2": 32,
                "dc-border-1": 28,
                "dc-border-2": 26,
                "dc-host-1": 10,
                "dc-host-2": 8,
            },
        },
        {
            "name": "DC-East-R2",
            "site_slug": "dc-east",
            "u_height": 42,
            "desc": "DC-East security + WAN rack",
            "devices": {
                "dc-fw-1": 40,
                "dc-fw-2": 38,
                "dc-ce-1": 30,
            },
        },
        {
            "name": "SP-Transport-R1",
            "site_slug": "dc-east",
            "u_height": 24,
            "desc": "Service provider transport — PE routers",
            "devices": {
                "sp-pe-1": 20,
                "sp-pe-2": 18,
            },
        },
        {
            "name": "Branch-01-R1",
            "site_slug": "branch-01",
            "u_height": 12,
            "desc": "Branch office rack — single CE + host",
            "devices": {
                "br-ce-1": 10,
                "br-host-1": 4,
            },
        },
        {
            "name": "DR-West-R1",
            "site_slug": "dr-west",
            "u_height": 42,
            "desc": "DR-West recovery rack — collapsed EVPN + security",
            "devices": {
                "dr-leaf-1": 40,
                "dr-leaf-2": 38,
                "dr-fw-1": 30,
                "dr-fw-2": 28,
                "dr-ce-1": 20,
                "dr-host-1": 10,
            },
        },
    ]

    # Rack roles
    rack_role_network = _get_or_create(
        nb.dcim.rack_roles,
        {"slug": "network"},
        {
            "name": "Network",
            "slug": "network",
            "color": "2196f3",
            "description": "Network equipment rack",
        },
        "rack role",
    )

    for rdef in rack_defs:
        site = nb.dcim.sites.get(slug=rdef["site_slug"])
        if not site:
            continue

        rack = _get_or_create(
            nb.dcim.racks,
            {"name": rdef["name"], "site_id": site.id},
            {
                "name": rdef["name"],
                "site": site.id,
                "tenant": tenant.id,
                "role": rack_role_network.id,
                "u_height": rdef["u_height"],
                "status": "active",
                "description": rdef["desc"],
            },
            "rack",
        )

        # Place devices in rack
        for dev_name, rack_u in rdef["devices"].items():
            device = nb.dcim.devices.get(name=dev_name)
            if device and (device.rack is None or device.rack.id != rack.id):
                device.rack = rack.id
                device.position = rack_u
                device.face = "front"
                device.tenant = tenant.id
                with contextlib.suppress(RequestError):
                    device.save()

    # ==================================================================
    # 6. RIRs + ASNs
    # ==================================================================
    print("6. RIRs and ASNs")

    rir = _get_or_create(
        nb.ipam.rirs,
        {"slug": "private"},
        {
            "name": "Private",
            "slug": "private",
            "is_private": True,
            "description": "Private AS numbers for lab use",
        },
        "RIR",
    )

    asn_defs = [
        (64500, "SP Transport AS"),
        (65000, "DC-East Spine AS"),
        (65001, "DC-East Leaf-1 AS"),
        (65002, "DC-East Leaf-2 AS"),
        (65003, "DC-East Border-1 AS"),
        (65004, "DC-East Border-2 AS"),
        (65100, "CE Routers AS (shared)"),
        (65201, "DR-West Leaf-1 AS"),
        (65202, "DR-West Leaf-2 AS"),
    ]

    for asn_num, desc in asn_defs:
        _get_or_create(
            nb.ipam.asns,
            {"asn": asn_num},
            {"asn": asn_num, "rir": rir.id, "description": desc, "tenant": tenant.id},
            "ASN",
        )

    # ==================================================================
    # 7. VRFs
    # ==================================================================
    print("7. VRFs")

    vrf_defs = [
        ("UNDERLAY", "underlay", "DC underlay routing (eBGP between spine/leaf)"),
        ("OVERLAY", "overlay", "DC overlay routing (iBGP EVPN between VTEPs)"),
        ("WAN-TRANSPORT", "wan-transport", "Service provider WAN transport"),
        ("MGMT", "mgmt", "Out-of-band management network"),
    ]

    for name, rd, desc in vrf_defs:
        _get_or_create(
            nb.ipam.vrfs,
            {"name": name},
            {
                "name": name,
                "rd": f"65000:{rd.replace('-', '')[:8]}",
                "tenant": tenant.id,
                "description": desc,
                "enforce_unique": False,
            },
            "VRF",
        )

    # ==================================================================
    # 8. VLAN Groups + VLANs
    # ==================================================================
    print("8. VLANs")

    dc_east_site = nb.dcim.sites.get(slug="dc-east")
    dr_west_site = nb.dcim.sites.get(slug="dr-west")

    vlan_group_dc = _get_or_create(
        nb.ipam.vlan_groups,
        {"slug": "dc-east-vlans"},
        {
            "name": "DC-East VLANs",
            "slug": "dc-east-vlans",
            "scope_type": "dcim.site",
            "scope_id": dc_east_site.id,
            "description": "VXLAN-mapped VLANs for DC-East fabric",
        },
        "VLAN group",
    )

    vlan_group_dr = _get_or_create(
        nb.ipam.vlan_groups,
        {"slug": "dr-west-vlans"},
        {
            "name": "DR-West VLANs",
            "slug": "dr-west-vlans",
            "scope_type": "dcim.site",
            "scope_id": dr_west_site.id,
            "description": "VXLAN-mapped VLANs for DR-West fabric",
        },
        "VLAN group",
    )

    vlan_defs = [
        (100, "SERVERS_A", vlan_group_dc, dc_east_site, "VNI 10100 — Compute VLAN A"),
        (200, "SERVERS_B", vlan_group_dc, dc_east_site, "VNI 10200 — Compute VLAN B"),
        (100, "SERVERS_DR", vlan_group_dr, dr_west_site, "VNI 10100 — DR Compute VLAN"),
        (999, "MGMT", None, None, "Management VLAN"),
    ]

    for vid, name, group, site, desc in vlan_defs:
        data = {
            "vid": vid,
            "name": name,
            "status": "active",
            "tenant": tenant.id,
            "description": desc,
        }
        if group:
            data["group"] = group.id
        if site:
            data["site"] = site.id

        existing = None
        if group:
            existing = nb.ipam.vlans.get(vid=vid, group_id=group.id)
        else:
            existing = nb.ipam.vlans.get(vid=vid, name=name)

        if not existing:
            with contextlib.suppress(RequestError):
                nb.ipam.vlans.create(data)
                print(f"    Created VLAN: {vid} {name}")

    # ==================================================================
    # 9. CIRCUIT PROVIDER + CIRCUITS
    # ==================================================================
    print("9. Circuits")

    provider = _get_or_create(
        nb.circuits.providers,
        {"slug": "lab-sp"},
        {
            "name": "Lab Service Provider",
            "slug": "lab-sp",
            "description": "Simulated SP transport (AS 64500)",
        },
        "provider",
    )

    circuit_type = _get_or_create(
        nb.circuits.circuit_types,
        {"slug": "wan-transport"},
        {
            "name": "WAN Transport",
            "slug": "wan-transport",
            "description": "Point-to-point WAN circuit",
        },
        "circuit type",
    )

    circuit_defs = [
        ("WAN-DC-PE1", "DC-East to SP PE-1 (primary)"),
        ("WAN-DC-PE2", "DC-East to SP PE-2 (secondary)"),
        ("WAN-BR-PE1", "Branch-01 to SP PE-1 (primary)"),
        ("WAN-BR-PE2", "Branch-01 to SP PE-2 (secondary)"),
        ("WAN-DR-PE1", "DR-West to SP PE-1 (primary)"),
        ("WAN-DR-PE2", "DR-West to SP PE-2 (secondary)"),
    ]

    for cid, desc in circuit_defs:
        _get_or_create(
            nb.circuits.circuits,
            {"cid": cid},
            {
                "cid": cid,
                "provider": provider.id,
                "type": circuit_type.id,
                "status": "active",
                "tenant": tenant.id,
                "description": desc,
                "commit_rate": 1000000,
            },
            "circuit",
        )

    # ==================================================================
    # 10. CONTACTS
    # ==================================================================
    print("10. Contacts")

    contact_role = _get_or_create(
        nb.tenancy.contact_roles,
        {"slug": "lab-admin"},
        {
            "name": "Lab Administrator",
            "slug": "lab-admin",
            "description": "Primary lab administrator",
        },
        "contact role",
    )

    owner_name = getattr(creds, "lab_owner_name", "") or "Lab Owner"
    owner_email = getattr(creds, "lab_owner_email", "") or ""
    contact_data = {"name": owner_name, "title": "Network Engineer / Lab Owner"}
    if owner_email:
        contact_data["email"] = owner_email

    contact = _get_or_create(
        nb.tenancy.contacts,
        {"name": owner_name},
        contact_data,
        "contact",
    )

    # Assign contact to tenant
    existing_assignments = list(
        nb.tenancy.contact_assignments.filter(object_type="tenancy.tenant", object_id=tenant.id)
    )
    if not existing_assignments:
        with contextlib.suppress(RequestError):
            nb.tenancy.contact_assignments.create(
                {
                    "object_type": "tenancy.tenant",
                    "object_id": tenant.id,
                    "contact": contact.id,
                    "role": contact_role.id,
                }
            )
            print("    Assigned contact to tenant")

    # ==================================================================
    # 11. TAGS for categorization
    # ==================================================================
    print("11. Tags")

    tag_defs = [
        ("managed", "4caf50", "Agent-managed device"),
        ("observed", "ff9800", "Agent-observed device (read-only)"),
        ("excluded", "f44336", "Excluded from agent management"),
        ("evpn-fabric", "2196f3", "Part of EVPN-VXLAN fabric"),
        ("wan-transport", "9c27b0", "WAN transport device"),
        ("ha-pair", "e91e63", "Part of an HA pair"),
    ]

    tags = {}
    for name, color, desc in tag_defs:
        tags[name] = _get_or_create(
            nb.extras.tags,
            {"slug": name},
            {"name": name, "slug": name, "color": color, "description": desc},
            "tag",
        )

    # Apply tags to devices based on agent boundary
    print("    Applying tags to devices...")
    tag_assignments = {
        "managed": [
            "dc-spine-1",
            "dc-spine-2",
            "dc-leaf-1",
            "dc-leaf-2",
            "dc-border-1",
            "dc-border-2",
            "br-ce-1",
            "dr-leaf-1",
            "dr-leaf-2",
        ],
        "excluded": [
            "dc-fw-1",
            "dc-fw-2",
            "dr-fw-1",
            "dr-fw-2",
            "dc-ce-1",
            "dr-ce-1",
            "sp-pe-1",
            "sp-pe-2",
        ],
        "evpn-fabric": [
            "dc-spine-1",
            "dc-spine-2",
            "dc-leaf-1",
            "dc-leaf-2",
            "dc-border-1",
            "dc-border-2",
            "dr-leaf-1",
            "dr-leaf-2",
        ],
        "wan-transport": ["dc-ce-1", "dr-ce-1", "sp-pe-1", "sp-pe-2", "br-ce-1"],
        "ha-pair": ["dc-fw-1", "dc-fw-2", "dr-fw-1", "dr-fw-2"],
    }

    for tag_name, device_names in tag_assignments.items():
        tag = tags.get(tag_name)
        if not tag:
            continue
        for dev_name in device_names:
            device = nb.dcim.devices.get(name=dev_name)
            if device:
                current_tags = [t.slug for t in device.tags] if device.tags else []
                if tag_name not in current_tags:
                    current_tags.append(tag_name)
                    device.tags = [{"slug": t} for t in current_tags]
                    with contextlib.suppress(RequestError):
                        device.save()

    # ==================================================================
    # 12. UPDATE PREFIXES — Assign tenant and VRF
    # ==================================================================
    print("12. Update Prefixes with tenant")

    for prefix in nb.ipam.prefixes.all():
        if prefix.tenant is None:
            prefix.tenant = tenant.id
            with contextlib.suppress(RequestError):
                prefix.save()

    # ==================================================================
    # Summary
    # ==================================================================
    print("\nEnterprise NetBox population complete.")
    print("  Organization: E-Conners Lab -> AI Infrastructure Lab")
    print("  Regions: North America -> US-East, US-Central, US-West")
    print("  Site Group: AI Infrastructure Lab (DC-East, Branch-01, DR-West)")
    print("  Racks: 5 (2x DC-East, 1x SP, 1x Branch, 1x DR)")
    print("  ASNs: 9 (private)")
    print("  VRFs: 4 (Underlay, Overlay, WAN, Mgmt)")
    print("  VLANs: 4 (SERVERS_A, SERVERS_B, SERVERS_DR, MGMT)")
    print("  Circuits: 6 WAN transport circuits")
    print("  Tags: 6 (managed, observed, excluded, evpn-fabric, wan, ha)")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Populate NetBox with enterprise organizational data"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    args = parser.parse_args()

    creds = require_credentials("netbox_url", "netbox_token")
    print(f"Connecting to NetBox at {creds.netbox_url}...")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)
    print("Connected.\n")

    if args.dry_run:
        print("DRY RUN — no changes will be made.\n")
        return

    populate_enterprise(nb, creds=creds)


if __name__ == "__main__":
    main()
