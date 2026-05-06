"""Config rendering engine.

Reads the YAML spec and renders per-device configs using Jinja2 templates.
Each device gets a config file at configs/generated/{device-name}.cfg.

Usage:
    python generator/render_configs.py                    # uses default spec path
    python generator/render_configs.py --spec path/to.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
OUTPUT_DIR = Path(__file__).parent.parent / "configs" / "generated"

# Template selection: (platform, role) -> template path relative to TEMPLATES_DIR
TEMPLATE_MAP: dict[tuple[str, str], str] = {
    ("arista_eos", "spine"): "arista/spine.j2",
    ("arista_eos", "leaf"): "arista/compute_leaf.j2",
    ("arista_eos", "border-leaf"): "arista/border_leaf.j2",
    ("cisco_iosxe", "ce"): "cisco/ce_router.j2",
    ("cisco_iosxe", "pe"): "cisco/pe_router.j2",
    ("fortinet_fortios", "firewall"): "fortinet/fortigate_ha.j2",
}

# Branch CE gets a separate template — identified by site context
BRANCH_CE_TEMPLATE = "cisco/branch_router.j2"

# Roles excluded from device config generation.
# - "host": end-host Linux boxes get bootstrapped, not config-rendered.
# - "cloud-vpn": its config lives in Terraform user_data (strongSwan), not Jinja templates.
EXCLUDED_ROLES = ("host", "cloud-vpn")


def load_spec(spec_path: Path) -> dict:
    """Load and return the YAML spec."""
    return yaml.safe_load(spec_path.read_text())


def get_all_network_devices(spec: dict) -> list[dict]:
    """Return all network devices (not hosts) from every section of the spec."""
    devices: list[dict] = []

    # Site devices
    for site_key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["role"] not in EXCLUDED_ROLES:
                devices.append({**dev, "_site_key": site_key, "_section": "site"})

    # WAN transport devices
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["role"] not in EXCLUDED_ROLES:
            devices.append({**dev, "_site_key": "wan", "_section": "wan_transport"})

    # Security devices (firewalls)
    for zone_key, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            devices.append({**dev, "_site_key": zone_key, "_section": "security"})

    return devices


def _find_device_in_spec(spec: dict, device_name: str) -> tuple[dict, str, str]:
    """Find a device by name and return (device_dict, site_key, section)."""
    for site_key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name:
                return dev, site_key, "site"

    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] == device_name:
            return dev, "wan", "wan_transport"

    for zone_key, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            if dev["name"] == device_name:
                return dev, zone_key, "security"

    raise ValueError(f"Device '{device_name}' not found in spec")


def _get_peer_ip(iface: dict) -> str:
    """Calculate the peer IP for a /31 link from the local IP."""
    if "ipv4" not in iface:
        return ""
    ip_str = iface["ipv4"].split("/")[0]
    octets = ip_str.split(".")
    last = int(octets[3])
    return ".".join(octets[:3]) + "." + str(last ^ 1)


def _ip_no_mask(ip_with_mask: str) -> str:
    """Strip prefix length from an IP address string."""
    return ip_with_mask.split("/")[0]


def _ip_mask(ip_with_mask: str) -> str:
    """Return just the prefix length."""
    return ip_with_mask.split("/")[1] if "/" in ip_with_mask else "32"


def _cidr_to_wildcard(prefix_len: int) -> str:
    """Convert a prefix length to a wildcard mask (for IOS-XE network statements)."""
    mask_int = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    wildcard_int = mask_int ^ 0xFFFFFFFF
    return ".".join(str((wildcard_int >> (8 * i)) & 0xFF) for i in range(3, -1, -1))


def _cidr_to_subnet(prefix_len: int) -> str:
    """Convert a prefix length to a subnet mask."""
    mask_int = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return ".".join(str((mask_int >> (8 * i)) & 0xFF) for i in range(3, -1, -1))


def _build_device_asn_map(spec: dict) -> dict[str, int]:
    """Build a name->ASN lookup across all devices in the spec.

    This lets templates resolve any peer's ASN from the spec rather than
    hardcoding or deriving from loop position.
    """
    asn_map: dict[str, int] = {}
    for _site_key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if "asn" in dev:
                asn_map[dev["name"]] = dev["asn"]
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if "asn" in dev:
            asn_map[dev["name"]] = dev["asn"]
    for _zone_key, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            if "asn" in dev:
                asn_map[dev["name"]] = dev["asn"]
    return asn_map


def _select_template(device: dict, site_key: str, section: str) -> str:
    """Select the Jinja2 template path for a device."""
    platform = device["platform"]
    role = device["role"]

    # Branch CE gets its own template
    if platform == "cisco_iosxe" and role == "ce" and site_key == "branch_01":
        return BRANCH_CE_TEMPLATE

    key = (platform, role)
    if key not in TEMPLATE_MAP:
        raise ValueError(
            f"No template for device '{device['name']}' (platform={platform}, role={role})"
        )
    return TEMPLATE_MAP[key]


def _build_context(spec: dict, device: dict, site_key: str, section: str) -> dict:
    """Build the Jinja2 template context for a device."""
    ctx: dict = {
        "device": device,
        "hostname": device["name"],
        "loopback0": device.get("loopback0", ""),
        "loopback0_ip": _ip_no_mask(device.get("loopback0", "")),
        "loopback0_mask": _ip_mask(device.get("loopback0", "32")),
        "asn": device.get("asn"),
        "interfaces": device.get("interfaces", []),
        "site_key": site_key,
        "section": section,
    }

    # Add helper functions to context
    ctx["peer_ip"] = _get_peer_ip
    ctx["ip_no_mask"] = _ip_no_mask
    ctx["ip_mask"] = _ip_mask
    ctx["cidr_to_wildcard"] = _cidr_to_wildcard
    ctx["cidr_to_subnet"] = _cidr_to_subnet

    # Management IP from bootstrap config
    from scripts.bootstrap_config import get_mgmt_gateway, get_mgmt_ips

    mgmt_ips = get_mgmt_ips()
    ctx["mgmt_ip"] = mgmt_ips.get(device["name"], "")
    ctx["mgmt_gateway"] = get_mgmt_gateway()

    # Build a global device-name-to-ASN lookup so templates can resolve peer ASNs
    # from the spec instead of hardcoding or deriving from loop position.
    device_asn_map = _build_device_asn_map(spec)
    ctx["device_asn"] = device_asn_map

    # Fabric config for DC/DR sites
    if site_key in ("dc_east", "dr_west") and section == "site":
        site_data = spec["sites"][site_key]
        ctx["fabric"] = site_data.get("fabric", {})
        ctx["overlay"] = site_data.get("fabric", {}).get("overlay", {})
        ctx["vxlan"] = site_data.get("fabric", {}).get("vxlan", {})

        # Per-device VTEP source IP (from vtep_sources map, falling back to vtep_source)
        vxlan_cfg = site_data.get("fabric", {}).get("vxlan", {})
        vtep_sources = vxlan_cfg.get("vtep_sources", {})
        ctx["vtep_ip"] = vtep_sources.get(device["name"], vxlan_cfg.get("vtep_source", ""))

        # Overlay ASN for consistent route-targets across all leaves in a fabric
        ctx["overlay_asn"] = (
            site_data.get("fabric", {}).get("overlay", {}).get("asn", device.get("asn"))
        )

        # Collect all spine devices in this site for leaf/border templates
        ctx["spines"] = [d for d in site_data.get("devices", []) if d["role"] == "spine"]

        # For collapsed designs (no spines): collect peer leaves for EVPN peering
        if not ctx["spines"]:
            ctx["evpn_peers"] = [
                d
                for d in site_data.get("devices", [])
                if d["role"] == "leaf" and d["name"] != device["name"]
            ]
        else:
            ctx["evpn_peers"] = []

    # Routing config for branch
    if site_key == "branch_01" and section == "site":
        site_data = spec["sites"][site_key]
        ctx["routing"] = site_data.get("routing", {})

    # Security config for firewalls
    if section == "security":
        zone_data = spec["security"][site_key]
        ctx["zones"] = zone_data.get("zones", [])
        ctx["policies"] = zone_data.get("policies", [])
        # Determine HA role: first firewall is active, second is standby
        fw_list = zone_data.get("firewalls", [])
        fw_index = next((i for i, f in enumerate(fw_list) if f["name"] == device["name"]), 0)
        ctx["ha_role"] = "active" if fw_index == 0 else "standby"
        ctx["ha_priority"] = 200 - (fw_index * 50)
        ctx["ha_peer"] = fw_list[1 - fw_index]["name"] if len(fw_list) > 1 else ""
        ctx["ha_group_id"] = 1 if site_key == "dc" else 2

    # For CE routers, find PE neighbors from interfaces
    if device["role"] == "ce":
        pe_neighbors = []
        for iface in device.get("interfaces", []):
            if iface.get("peer", "").startswith("sp-pe"):
                peer_name = iface.get("peer", "")
                pe_neighbors.append(
                    {
                        "interface": iface["name"],
                        "local_ip": _ip_no_mask(iface.get("ipv4", "")),
                        "peer_ip": _get_peer_ip(iface),
                        "peer_name": peer_name,
                        "remote_as": device_asn_map.get(peer_name, 0),
                    }
                )
        ctx["pe_neighbors"] = pe_neighbors

        # IPsec tunnels terminating on this CE — pulled from cloud_aws site.
        # The actual PSK is injected at push time (scripts/push_configs._inject_aws_psk);
        # the rendered config carries only the marker so it stays commit-safe.
        cloud_site = spec.get("sites", {}).get("cloud_aws", {})
        ctx["vpn_tunnels"] = [
            t for t in cloud_site.get("vpn_tunnels", []) if t.get("local_device") == device["name"]
        ]
        ctx["psk_marker"] = "__AWS_VPN_PSK__"

    return ctx


def render_device_config(spec: dict, device_name: str) -> str:
    """Render the config for a single device and return as a string."""
    device, site_key, section = _find_device_in_spec(spec, device_name)
    template_path = _select_template(device, site_key, section)
    context = _build_context(spec, device, site_key, section)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    template = env.get_template(template_path)
    return template.render(**context)


def render_all_configs(spec: dict, output_dir: Path) -> list[Path]:
    """Render configs for all network devices and write to files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    devices = get_all_network_devices(spec)
    for dev_info in devices:
        device_name = dev_info["name"]
        config = render_device_config(spec, device_name)

        if not config.strip():
            print(f"WARNING: Empty config for {device_name}", file=sys.stderr)
            continue

        out_path = output_dir / f"{device_name}.cfg"
        out_path.write_text(config)
        written.append(out_path)

    return written


def main() -> None:
    """CLI entrypoint — render all device configs from the spec."""
    parser = argparse.ArgumentParser(description="Render device configs from YAML spec")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH, help="Path to YAML spec")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec file not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = load_spec(args.spec)
    written = render_all_configs(spec, args.output)
    print(f"Rendered {len(written)} device configs to {args.output}")
    for p in sorted(written):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
