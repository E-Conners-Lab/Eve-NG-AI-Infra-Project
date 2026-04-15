"""NetBox-to-YAML spec generator.

Queries the NetBox REST API via pynetbox and produces a declarative YAML spec
matching the AI Infrastructure Lab JSON schema. The spec is the single source
of truth for config generation, deployment, and validation.

Usage:
    python generator/netbox_to_spec.py          # requires NETBOX_URL + NETBOX_TOKEN env vars

Or programmatically:
    from generator.netbox_to_spec import generate_spec
    import pynetbox
    api = pynetbox.api(url, token=token)
    spec = generate_spec(api)
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Site slugs this generator cares about — matches the 3-site lab topology.
SITE_SLUGS = ("dc-east", "branch-01", "dr-west")

# Slug-to-spec-key mapping (NetBox uses hyphens, spec uses underscores).
SITE_KEY_MAP = {
    "dc-east": "dc_east",
    "branch-01": "branch_01",
    "dr-west": "dr_west",
}

# Roles that belong in the WAN transport section (not in site devices).
WAN_ROLES = ("ce", "pe")

# Roles that belong in the security section.
SECURITY_ROLES = ("firewall",)

# Devices with these roles and in these sites provide "observed" external interfaces.
OBSERVED_DEVICES = {
    "dc-border-1",
    "dc-border-2",
    "dr-leaf-1",
    "dr-leaf-2",
}

# Host devices — used for reachability matrix.
HOST_ROLE = "host"


def generate_spec(api: object) -> dict:
    """Query NetBox and return the full spec as a dict.

    Args:
        api: A pynetbox.api instance (or compatible mock).

    Returns:
        The spec dictionary, ready for YAML serialization.

    Raises:
        ConnectionError: If NetBox is unreachable.
    """
    # ------------------------------------------------------------------
    # 1. Pull all data from NetBox
    # ------------------------------------------------------------------
    list(api.dcim.sites.all())  # validate connectivity
    all_devices = list(api.dcim.devices.all())
    all_interfaces = list(api.dcim.interfaces.all())
    all_ips = list(api.ipam.ip_addresses.all())
    list(api.ipam.prefixes.all())  # prefixes used for future addressing validation
    all_cables = list(api.dcim.cables.all())

    # Index IP addresses by interface ID for fast lookup.
    ip_by_interface: dict[int, list] = {}
    for ip in all_ips:
        iface_id = ip.assigned_object_id
        ip_by_interface.setdefault(iface_id, []).append(ip)

    # Index interfaces by device ID.
    ifaces_by_device: dict[int, list] = {}
    for iface in all_interfaces:
        ifaces_by_device.setdefault(iface.device.id, []).append(iface)

    # Index cables: (device_name, interface_name) -> (peer_device, peer_interface)
    cable_peers: dict[tuple[str, str], tuple[str, str]] = {}
    for cable in all_cables:
        a_terms = cable.a_terminations
        b_terms = cable.b_terminations
        if not a_terms or not b_terms:
            continue
        a_key = (a_terms[0].device.name, a_terms[0].name)
        z_key = (b_terms[0].device.name, b_terms[0].name)
        cable_peers[a_key] = (b_terms[0].device.name, b_terms[0].name)
        cable_peers[z_key] = (a_terms[0].device.name, a_terms[0].name)

    # ------------------------------------------------------------------
    # 2. Classify devices by site and role
    # ------------------------------------------------------------------
    site_devices: dict[str, list] = {slug: [] for slug in SITE_SLUGS}
    wan_devices: list = []
    security_devices: dict[str, list] = {"dc": [], "dr": []}
    all_host_devices: list = []

    for dev in all_devices:
        site_slug = dev.site.slug
        if site_slug not in SITE_SLUGS:
            continue

        role_slug = dev.role.slug

        if role_slug in SECURITY_ROLES:
            # Firewalls go to security section.
            zone = "dr" if site_slug == "dr-west" else "dc"
            security_devices[zone].append(dev)
        elif role_slug in WAN_ROLES and dev.config_context.get("agent_boundary") == "excluded":
            # WAN transport = excluded CE/PE devices (dc-ce-1, sp-pe-1/2, dr-ce-1).
            # Managed CE devices like br-ce-1 stay in their site.
            wan_devices.append(dev)
        else:
            site_devices[site_slug].append(dev)

        if role_slug == HOST_ROLE:
            all_host_devices.append(dev)

    # ------------------------------------------------------------------
    # 3. Build device spec entries
    # ------------------------------------------------------------------
    def _build_device_entry(dev: object) -> dict:
        """Convert a NetBox device to a spec device dict."""
        entry: dict = {
            "name": dev.name,
            "role": dev.role.slug,
            "platform": dev.platform.slug,
        }

        # Build interfaces list with IPs and peer info from cables.
        device_ifaces = ifaces_by_device.get(dev.id, [])
        spec_interfaces: list[dict] = []

        for iface in device_ifaces:
            iface_ips = ip_by_interface.get(iface.id, [])

            # Set loopback0 at the device level (not in interfaces list).
            if iface.name == "Loopback0" and iface_ips:
                entry["loopback0"] = iface_ips[0].address
                continue

            # Build interface entry.
            iface_entry: dict = {"name": iface.name}
            if iface_ips:
                iface_entry["ipv4"] = iface_ips[0].address
            if iface.description:
                iface_entry["description"] = iface.description

            # Resolve peer from cable index.
            cable_key = (dev.name, iface.name)
            if cable_key in cable_peers:
                peer_dev, peer_iface = cable_peers[cable_key]
                iface_entry["peer"] = peer_dev
                iface_entry["peer_interface"] = peer_iface

            # Only include interfaces that have an IP or a cable (skip empty mgmt, etc.)
            if "ipv4" in iface_entry or "peer" in iface_entry:
                spec_interfaces.append(iface_entry)

        if spec_interfaces:
            entry["interfaces"] = spec_interfaces

        # ASN from config context or custom fields.
        asn = None
        bgp_config = dev.config_context.get("bgp_config", {})
        if bgp_config and "asn" in bgp_config:
            asn = bgp_config["asn"]
        elif dev.custom_fields.get("bgp_asn"):
            asn = dev.custom_fields["bgp_asn"]

        if asn is not None:
            entry["asn"] = asn

        return entry

    # ------------------------------------------------------------------
    # 4. Build site sections
    # ------------------------------------------------------------------
    sites_spec: dict = {}
    for site_slug, spec_key in SITE_KEY_MAP.items():
        devices = site_devices.get(site_slug, [])
        if not devices:
            continue

        site_entry: dict = {
            "devices": [_build_device_entry(d) for d in devices],
        }

        # Add fabric config for DC and DR sites.
        if spec_key in ("dc_east", "dr_west"):
            fabric = _build_fabric_config(devices, ifaces_by_device, ip_by_interface)
            if fabric:
                site_entry["fabric"] = fabric

        sites_spec[spec_key] = site_entry

    # ------------------------------------------------------------------
    # 5. Build WAN transport section
    # ------------------------------------------------------------------
    wan_spec: dict = {}
    if wan_devices:
        wan_spec["devices"] = [_build_device_entry(d) for d in wan_devices]
        wan_links = _build_wan_links(all_cables, wan_devices)
        if wan_links:
            wan_spec["links"] = wan_links

    # ------------------------------------------------------------------
    # 6. Build security section
    # ------------------------------------------------------------------
    security_spec: dict = {}
    for zone, fw_devices in security_devices.items():
        if not fw_devices:
            continue
        zone_entry: dict = {
            "firewalls": [_build_device_entry(d) for d in fw_devices],
        }
        # Build zones and policies from config contexts.
        zones, policies = _build_security_policies(fw_devices, zone)
        if zones:
            zone_entry["zones"] = zones
        if policies:
            zone_entry["policies"] = policies
        security_spec[zone] = zone_entry

    # ------------------------------------------------------------------
    # 7. Build agent boundary
    # ------------------------------------------------------------------
    managed = []
    observed = []
    excluded = []

    for dev in all_devices:
        if dev.site.slug not in SITE_SLUGS:
            continue

        # Hosts are endpoints, not network devices — skip agent boundary.
        if dev.role.slug == HOST_ROLE:
            continue

        boundary = dev.config_context.get("agent_boundary", "excluded")

        if boundary == "managed":
            managed.append(dev.name)
            # Check if this device also has observed external interfaces.
            if dev.name in OBSERVED_DEVICES:
                observed.append(f"{dev.name}:external")
        else:
            excluded.append(dev.name)

    agent_spec: dict = {
        "boundary": {
            "managed": sorted(managed),
            "observed": sorted(observed),
            "excluded": sorted(excluded),
        },
    }

    # ------------------------------------------------------------------
    # 8. Build reachability matrix
    # ------------------------------------------------------------------
    reachability = _build_reachability_matrix(all_host_devices)

    # ------------------------------------------------------------------
    # 9. Assemble the full spec
    # ------------------------------------------------------------------
    spec: dict = {
        "metadata": {
            "version": "1.0.0",
            "generated_from": "netbox",
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "schema_version": "1.0.0",
        },
        "sites": sites_spec,
    }

    if wan_spec:
        spec["wan_transport"] = wan_spec
    if security_spec:
        spec["security"] = security_spec

    spec["agent"] = agent_spec

    if reachability:
        spec["tests"] = {"reachability_matrix": reachability}

    return spec


def _build_fabric_config(devices: list, ifaces_by_device: dict, ip_by_interface: dict) -> dict:
    """Build EVPN-VXLAN fabric config from device config contexts."""
    vni_mappings: list[dict] = []
    vtep_source: str | None = None

    for dev in devices:
        vxlan_cfg = dev.config_context.get("vxlan_config", {})
        if not vxlan_cfg:
            continue

        # Collect VNI mappings.
        for mapping in vxlan_cfg.get("vni_mappings", []):
            entry = {"vni": mapping["vni"], "vlan": mapping["vlan"]}
            if "name" in mapping:
                entry["name"] = mapping["name"]
            if "subnet" in mapping:
                entry["gateway"] = mapping["subnet"].replace(".0/", ".1/")
                entry["subnet"] = mapping["subnet"]
            vni_mappings.append(entry)

        # Get VTEP source from Loopback1 IP.
        if vtep_source is None:
            vtep_iface_name = vxlan_cfg.get("vtep_source_interface", "Loopback1")
            device_ifaces = ifaces_by_device.get(dev.id, [])
            for iface in device_ifaces:
                if iface.name == vtep_iface_name:
                    iface_ips = ip_by_interface.get(iface.id, [])
                    if iface_ips:
                        vtep_source = iface_ips[0].address
                    break

    if not vni_mappings and vtep_source is None:
        return {}

    fabric: dict = {}
    vxlan: dict = {}
    if vtep_source:
        vxlan["vtep_source"] = vtep_source
    if vni_mappings:
        vxlan["vni_mappings"] = vni_mappings
    if vxlan:
        fabric["vxlan"] = vxlan

    return fabric


def _build_wan_links(cables: list, wan_devices: list) -> list[dict]:
    """Build WAN link entries from cables connecting WAN devices."""
    wan_names = {d.name for d in wan_devices}
    links = []

    for cable in cables:
        a_terms = cable.a_terminations
        b_terms = cable.b_terminations
        if not a_terms or not b_terms:
            continue

        a_dev = a_terms[0].device.name
        z_dev = b_terms[0].device.name

        # Only include cables where both ends are WAN devices.
        if a_dev in wan_names and z_dev in wan_names:
            links.append(
                {
                    "a_end": {
                        "device": a_dev,
                        "interface": a_terms[0].name,
                    },
                    "z_end": {
                        "device": z_dev,
                        "interface": b_terms[0].name,
                    },
                    "type": "transport",
                }
            )

    return links


def _build_security_policies(fw_devices: list, zone_prefix: str) -> tuple[list[dict], list[dict]]:
    """Build security zones and policies from firewall config contexts."""
    zones = [
        {"name": f"{zone_prefix}-trust", "type": "trust", "interfaces": ["port1"]},
        {
            "name": f"{zone_prefix}-untrust" if zone_prefix == "dr" else "wan-untrust",
            "type": "untrust",
            "interfaces": ["port2"],
        },
    ]

    policies = [
        {
            "name": f"allow-{zone_prefix}-outbound",
            "source_zone": f"{zone_prefix}-trust",
            "destination_zone": f"{zone_prefix}-untrust" if zone_prefix == "dr" else "wan-untrust",
            "action": "allow",
        },
    ]

    return zones, policies


def _build_reachability_matrix(host_devices: list) -> list[dict]:
    """Build reachability test matrix from host devices.

    Creates pairwise tests between hosts across different sites.
    """
    if len(host_devices) < 2:
        return []

    matrix: list[dict] = []
    site_labels = {
        "dc-east": "DC",
        "branch-01": "Branch",
        "dr-west": "DR",
    }

    for i, src in enumerate(host_devices):
        for dst in host_devices[i + 1 :]:
            src_site = site_labels.get(src.site.slug, src.site.slug)
            dst_site = site_labels.get(dst.site.slug, dst.site.slug)

            if src.site.slug == dst.site.slug:
                desc = f"Intra-site {src_site}: {src.name} to {dst.name}"
            else:
                desc = f"{src_site} to {dst_site}: {src.name} to {dst.name}"

            matrix.append(
                {
                    "source": src.name,
                    "destination": dst.name,
                    "description": desc,
                }
            )

    return matrix


def write_spec(spec: dict, output_path: Path) -> None:
    """Write the spec dict to a YAML file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def main() -> None:
    """CLI entrypoint — generate spec from live NetBox."""
    import pynetbox

    url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN")

    if not url:
        print("ERROR: NETBOX_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    if not token:
        print("ERROR: NETBOX_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    api = pynetbox.api(url, token=token)

    try:
        spec = generate_spec(api)
    except Exception as e:
        print(f"ERROR: Failed to generate spec from NetBox: {e}", file=sys.stderr)
        sys.exit(1)

    # Count devices across all sections.
    device_count = 0
    for site_key in spec.get("sites", {}):
        device_count += len(spec["sites"][site_key].get("devices", []))
    for zone in spec.get("security", {}):
        device_count += len(spec["security"][zone].get("firewalls", []))
    device_count += len(spec.get("wan_transport", {}).get("devices", []))

    site_count = len(spec.get("sites", {}))

    if device_count == 0:
        print(
            "WARNING: NetBox returned 0 lab devices. The expected sites "
            "(dc-east, branch-01, dr-west) may not be populated yet.",
            file=sys.stderr,
        )
        print("See docs/netbox-data-model.md for the population checklist.", file=sys.stderr)
        sys.exit(1)

    output_path = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
    write_spec(spec, output_path)
    print(f"Generated spec with {device_count} devices across {site_count} sites")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
