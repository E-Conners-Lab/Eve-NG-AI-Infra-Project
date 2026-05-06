"""NetBox-to-YAML spec generator.


Queries the NetBox REST API via pynetbox and produces a declarative YAML spec
matching the AI Infrastructure Lab JSON schema.

ALL data comes from NetBox — config contexts carry fabric, VXLAN, security,
and agent boundary metadata. If it's not in NetBox, it doesn't exist.

Usage:
    python generator/netbox_to_spec.py    # uses .env for credentials
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Site slugs this generator cares about.
logger = logging.getLogger(__name__)

SITE_SLUGS = ("dc-east", "branch-01", "dr-west", "cloud-aws")

# Slug-to-spec-key mapping (NetBox hyphens → spec underscores).
SITE_KEY_MAP = {
    "dc-east": "dc_east",
    "branch-01": "branch_01",
    "dr-west": "dr_west",
    "cloud-aws": "cloud_aws",
}

WAN_ROLES = ("ce", "pe")
SECURITY_ROLES = ("firewall",)
HOST_ROLE = "host"
CLOUD_VPN_ROLE = "cloud-vpn"


def generate_spec(api: object) -> dict:
    """Query NetBox and return the full spec as a dict."""

    # 1. Pull all data from NetBox
    list(api.dcim.sites.all())  # validate connectivity
    all_devices = list(api.dcim.devices.all())
    all_interfaces = list(api.dcim.interfaces.all())
    all_ips = list(api.ipam.ip_addresses.all())
    all_cables = list(api.dcim.cables.all())

    # Index IPs by interface ID
    ip_by_iface: dict[int, list] = {}
    for ip in all_ips:
        ip_by_iface.setdefault(ip.assigned_object_id, []).append(ip)

    # Index interfaces by device ID
    ifaces_by_dev: dict[int, list] = {}
    for iface in all_interfaces:
        ifaces_by_dev.setdefault(iface.device.id, []).append(iface)

    # Index cables: (device, interface) → (peer_device, peer_interface)
    cable_peers: dict[tuple[str, str], tuple[str, str]] = {}
    for cable in all_cables:
        a, b = cable.a_terminations, cable.b_terminations
        if not a or not b:
            continue
        cable_peers[(a[0].device.name, a[0].name)] = (b[0].device.name, b[0].name)
        cable_peers[(b[0].device.name, b[0].name)] = (a[0].device.name, a[0].name)

    # 2. Classify devices
    site_devices: dict[str, list] = {s: [] for s in SITE_SLUGS}
    wan_devices: list = []
    security_devices: dict[str, list] = {"dc": [], "dr": []}
    all_hosts: list = []

    for dev in all_devices:
        slug = dev.site.slug
        if slug not in SITE_SLUGS:
            continue
        role = dev.role.slug
        if role in SECURITY_ROLES:
            security_devices["dr" if slug == "dr-west" else "dc"].append(dev)
        elif role in WAN_ROLES and dev.config_context.get("agent_boundary") == "excluded":
            wan_devices.append(dev)
        else:
            site_devices[slug].append(dev)
        if role == HOST_ROLE:
            all_hosts.append(dev)

    # 3. Device entry builder
    def _device(dev: object) -> dict:
        entry: dict = {
            "name": dev.name,
            "role": dev.role.slug,
            "platform": dev.platform.slug,
        }

        # Interfaces with IPs and cable peers
        spec_ifaces: list[dict] = []
        for iface in ifaces_by_dev.get(dev.id, []):
            ips = ip_by_iface.get(iface.id, [])
            if iface.name == "Loopback0" and ips:
                entry["loopback0"] = ips[0].address
                continue

            ie: dict = {"name": iface.name}
            if ips:
                ie["ipv4"] = ips[0].address
            if iface.description:
                ie["description"] = iface.description

            peer = cable_peers.get((dev.name, iface.name))
            if peer:
                ie["peer"] = peer[0]
                ie["peer_interface"] = peer[1]

            # VLAN/VNI from config context mappings
            vxlan_cfg = dev.config_context.get("vxlan_config", {})
            for m in vxlan_cfg.get("vni_mappings", []):
                if iface.description and m.get("name", "") in iface.description:
                    ie["vlan"] = m["vlan"]
                    ie["vni"] = m["vni"]
                    break

            if "ipv4" in ie or "peer" in ie:
                spec_ifaces.append(ie)

        if spec_ifaces:
            entry["interfaces"] = spec_ifaces

        # ASN from config context
        bgp = dev.config_context.get("bgp_config", {})
        if bgp.get("asn"):
            entry["asn"] = bgp["asn"]

        return entry

    # 4. Build fabric config from config contexts
    def _fabric(devices: list) -> dict:
        fabric: dict = {}
        vni_all: list[dict] = []
        vtep_sources: dict[str, str] = {}
        overlay: dict = {}
        underlay_asn = None

        for dev in devices:
            ctx = dev.config_context

            # Underlay ASN (from spines)
            bgp = ctx.get("bgp_config", {})
            if bgp.get("role") == "rr" and underlay_asn is None:
                underlay_asn = bgp.get("asn")
                overlay["asn"] = underlay_asn
                # Resolve router_id from Loopback0 IP
                lo_ip = ""
                for lo_iface in ifaces_by_dev.get(dev.id, []):
                    if lo_iface.name == "Loopback0":
                        lo_ips = ip_by_iface.get(lo_iface.id, [])
                        if lo_ips:
                            lo_ip = lo_ips[0].address.split("/")[0]
                        break
                overlay["router_id"] = lo_ip

            # EVPN overlay config (from spines or peer leaves)
            evpn = ctx.get("evpn_overlay", {})

            # Overlay ASN for route-targets — from evpn_overlay.asn if no spine set it
            if evpn.get("asn") and "asn" not in overlay:
                overlay["asn"] = evpn["asn"]

            if evpn.get("neighbors") and "neighbors" not in overlay:
                overlay["neighbors"] = []
            for n in evpn.get("neighbors", []):
                # Avoid duplicates
                existing_addrs = {x["address"] for x in overlay.get("neighbors", [])}
                if n["address"] not in existing_addrs:
                    overlay.setdefault("neighbors", []).append(
                        {
                            "address": n["address"],
                            "remote_as": n["remote_as"],
                            "description": n.get("description", ""),
                            "address_families": ["evpn"],
                        }
                    )

            # VXLAN config
            vxlan = ctx.get("vxlan_config", {})
            if vxlan.get("vtep_ip"):
                vtep_sources[dev.name] = vxlan["vtep_ip"]
            for m in vxlan.get("vni_mappings", []):
                # Deduplicate by VNI
                if not any(v["vni"] == m["vni"] for v in vni_all):
                    entry = {"vni": m["vni"], "vlan": m["vlan"]}
                    if "name" in m:
                        entry["name"] = m["name"]
                    if "gateway" in m:
                        entry["gateway"] = m["gateway"]
                    if "subnet" in m:
                        entry["subnet"] = m["subnet"]
                    vni_all.append(entry)

        if underlay_asn:
            fabric["underlay"] = {"asn": underlay_asn}
        if overlay:
            fabric["overlay"] = overlay
        if vtep_sources or vni_all:
            vxlan_spec: dict = {}
            if vtep_sources:
                vxlan_spec["vtep_sources"] = vtep_sources
            if vni_all:
                vxlan_spec["vni_mappings"] = vni_all
            fabric["vxlan"] = vxlan_spec

        return fabric

    # 5. Build security from config contexts
    def _security(fw_devices: list) -> tuple[list, list]:
        zones: list[dict] = []
        policies: list[dict] = []
        seen_zones: set[str] = set()
        seen_policies: set[str] = set()

        for dev in fw_devices:
            ctx = dev.config_context
            for z in ctx.get("security_zones", []):
                if z["name"] not in seen_zones:
                    zones.append(z)
                    seen_zones.add(z["name"])
            for p in ctx.get("security_policies", []):
                if p["name"] not in seen_policies:
                    policies.append(p)
                    seen_policies.add(p["name"])

        return zones, policies

    # 6. Build routing section for branch
    def _branch_routing(devices: list) -> dict:
        for dev in devices:
            bgp = dev.config_context.get("bgp_config", {})
            if bgp.get("asn") and dev.role.slug == "ce":
                lo = None
                for iface in ifaces_by_dev.get(dev.id, []):
                    if iface.name == "Loopback0":
                        ips = ip_by_iface.get(iface.id, [])
                        if ips:
                            lo = ips[0].address.split("/")[0]
                        break

                return {
                    "asn": bgp["asn"],
                    "router_id": lo or "",
                }
        return {}

    # 6b. Build cloud site (vpn_tunnels live in any cloud-vpn device's config_context)
    def _build_cloud_site(devs: list) -> dict:
        tunnels: list[dict] = []
        for dev in devs:
            if dev.role.slug != CLOUD_VPN_ROLE:
                continue
            for tunnel in dev.config_context.get("vpn_tunnels", []) or []:
                tunnels.append(tunnel)
        return {
            "devices": [_device(d) for d in devs],
            "vpn_tunnels": tunnels,
        }

    # 7. Assemble sites
    sites_spec: dict = {}
    for site_slug, spec_key in SITE_KEY_MAP.items():
        devs = site_devices.get(site_slug, [])
        if not devs:
            continue

        if spec_key == "cloud_aws":
            sites_spec[spec_key] = _build_cloud_site(devs)
            continue

        site_entry: dict = {"devices": [_device(d) for d in devs]}

        if spec_key in ("dc_east", "dr_west"):
            fab = _fabric(devs)
            if fab:
                site_entry["fabric"] = fab

        if spec_key == "branch_01":
            routing = _branch_routing(devs)
            if routing:
                site_entry["routing"] = routing

        # Build links from device interface peers
        links: list[dict] = []
        seen: set[tuple] = set()
        for d in site_entry["devices"]:
            for iface in d.get("interfaces", []):
                peer = iface.get("peer")
                piface = iface.get("peer_interface")
                if not peer or not piface:
                    continue
                key = tuple(sorted([(d["name"], iface["name"]), (peer, piface)]))
                if key in seen:
                    continue
                seen.add(key)
                link: dict = {
                    "a_end": {"device": d["name"], "interface": iface["name"]},
                    "z_end": {"device": peer, "interface": piface},
                    "type": "p2p",
                }
                if "ipv4" in iface:
                    link["a_end"]["ipv4"] = iface["ipv4"]
                links.append(link)
        if links:
            site_entry["links"] = links

        sites_spec[spec_key] = site_entry

    # 8. WAN transport
    wan_spec: dict = {}
    if wan_devices:
        wan_devs = [_device(d) for d in wan_devices]
        wan_spec["devices"] = wan_devs
        # Build WAN links
        wlinks: list[dict] = []
        wseen: set[tuple] = set()
        wan_names = {d["name"] for d in wan_devs}
        for d in wan_devs:
            for iface in d.get("interfaces", []):
                peer = iface.get("peer")
                piface = iface.get("peer_interface")
                if not peer or not piface or peer not in wan_names:
                    continue
                key = tuple(sorted([(d["name"], iface["name"]), (peer, piface)]))
                if key in wseen:
                    continue
                wseen.add(key)
                wlinks.append(
                    {
                        "a_end": {"device": d["name"], "interface": iface["name"]},
                        "z_end": {"device": peer, "interface": piface},
                        "type": "transport",
                    }
                )
        if wlinks:
            wan_spec["links"] = wlinks

    # 9. Security
    security_spec: dict = {}
    for zone, fws in security_devices.items():
        if not fws:
            continue
        zone_entry: dict = {"firewalls": [_device(d) for d in fws]}
        zones, policies = _security(fws)
        if zones:
            zone_entry["zones"] = zones
        if policies:
            zone_entry["policies"] = policies
        security_spec[zone] = zone_entry

    # 10. Agent boundary — from config contexts, not hardcoded
    managed, observed, excluded = [], [], []
    for dev in all_devices:
        if dev.site.slug not in SITE_SLUGS or dev.role.slug == HOST_ROLE:
            continue
        ctx = dev.config_context
        boundary = ctx.get("agent_boundary", "excluded")
        if boundary == "managed":
            managed.append(dev.name)
        else:
            excluded.append(dev.name)
        # Observed interfaces are collected regardless of boundary — an excluded
        # device can still expose interfaces the agent watches (e.g. dc-ce-1:Tunnel0).
        for obs_iface in ctx.get("observed_interfaces", []) or []:
            observed.append(f"{dev.name}:{obs_iface}")

    agent_spec: dict = {
        "boundary": {
            "managed": sorted(managed),
            "observed": sorted(observed),
            "excluded": sorted(excluded),
        },
    }

    # Agent thresholds + compliance from config context (stored on all managed devices)
    for dev in all_devices:
        ctx = dev.config_context
        if "agent_thresholds" in ctx and "thresholds" not in agent_spec:
            agent_spec["thresholds"] = ctx["agent_thresholds"]
        if "compliance_rules" in ctx and "compliance_rules" not in agent_spec:
            agent_spec["compliance_rules"] = ctx["compliance_rules"]

    # 11. Reachability matrix
    reachability = _build_reachability_matrix(all_hosts)

    # 12. Assemble
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


def _build_reachability_matrix(hosts: list) -> list[dict]:
    """Build reachability test matrix from host devices."""
    if len(hosts) < 2:
        return []
    labels = {"dc-east": "DC", "branch-01": "Branch", "dr-west": "DR"}
    matrix: list[dict] = []
    for i, src in enumerate(hosts):
        for dst in hosts[i + 1 :]:
            s = labels.get(src.site.slug, src.site.slug)
            d = labels.get(dst.site.slug, dst.site.slug)
            desc = (
                f"Intra-site {s}: {src.name} to {dst.name}"
                if src.site.slug == dst.site.slug
                else f"{s} to {d}: {src.name} to {dst.name}"
            )
            matrix.append({"source": src.name, "destination": dst.name, "description": desc})
    return matrix


def write_spec(spec: dict, output_path: Path) -> None:
    """Write the spec dict to a YAML file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def main() -> None:
    """CLI entrypoint — generate spec from live NetBox."""
    import pynetbox

    from scripts.credentials import require_credentials

    creds = require_credentials("netbox_url", "netbox_token")
    api = pynetbox.api(creds.netbox_url, token=creds.netbox_token)

    try:
        spec = generate_spec(api)
    except Exception as e:
        logger.exception("Error: %s", e if "e" in dir() else "unknown")
        print(f"ERROR: Failed to generate spec from NetBox: {e}", file=sys.stderr)
        sys.exit(1)

    # Count devices
    count = 0
    for sk in spec.get("sites", {}):
        count += len(spec["sites"][sk].get("devices", []))
    for z in spec.get("security", {}):
        count += len(spec["security"][z].get("firewalls", []))
    count += len(spec.get("wan_transport", {}).get("devices", []))

    if count == 0:
        print("WARNING: NetBox returned 0 lab devices.", file=sys.stderr)
        sys.exit(1)

    output = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
    write_spec(spec, output)
    print(f"Generated spec with {count} devices across {len(spec.get('sites', {}))} sites")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
