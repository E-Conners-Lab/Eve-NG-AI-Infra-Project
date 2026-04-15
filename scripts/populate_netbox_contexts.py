"""Populate NetBox config contexts from the YAML spec.

Reads device names, ASNs, IPs, and topology data from the spec and pushes
config contexts to NetBox. No hardcoded device names, IPs, or ASNs —
everything derives from the spec and bootstrap config.

Run AFTER populate_netbox.py (which creates devices, interfaces, IPs, cables).

Usage:
    python -m scripts.populate_netbox_contexts
"""

from __future__ import annotations

import logging
import sys

import pynetbox

from scripts.bootstrap_config import get_mgmt_ips, get_shared_overlay_asn
from scripts.credentials import require_credentials

logger = logging.getLogger(__name__)

SPEC_KEYS_TO_SITE = {
    "dc_east": "dc-east",
    "branch_01": "branch-01",
    "dr_west": "dr-west",
}

FABRIC_SITES = ("dc_east", "dr_west")


def _update_context(nb: pynetbox.api, device_name: str, context: dict) -> None:
    """Merge context data into a device's local_context_data."""
    dev = nb.dcim.devices.get(name=device_name)
    if not dev:
        logger.warning("Device %s not found in NetBox", device_name)
        return
    existing = dev.local_context_data or {}
    existing.update(context)
    dev.local_context_data = existing
    dev.save()


def _get_loopback_ip(spec: dict, device_name: str) -> str:
    """Find a device's Loopback0 IP from the spec."""
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name and "loopback0" in dev:
                return dev["loopback0"].split("/")[0]
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] == device_name and "loopback0" in dev:
            return dev["loopback0"].split("/")[0]
    return ""


def populate_contexts(nb: pynetbox.api, spec: dict) -> None:
    """Push config contexts to all devices based on the spec."""
    mgmt_ips = get_mgmt_ips()
    shared_overlay_asn = get_shared_overlay_asn()
    boundary = spec.get("agent", {}).get("boundary", {})
    managed_set = set(boundary.get("managed", []))
    excluded_set = set(boundary.get("excluded", []))

    # Thresholds and compliance (applied to all managed devices)
    thresholds = spec.get("agent", {}).get("thresholds", {})
    compliance_rules = spec.get("agent", {}).get("compliance_rules", [])

    # ------------------------------------------------------------------
    # 1. Process site devices
    # ------------------------------------------------------------------
    for spec_key, site_data in spec.get("sites", {}).items():
        fabric = site_data.get("fabric", {})
        overlay = fabric.get("overlay", {})
        vxlan = fabric.get("vxlan", {})

        for dev in site_data.get("devices", []):
            name = dev["name"]
            role = dev["role"]
            ctx: dict = {}

            # Agent boundary
            if name in managed_set:
                ctx["agent_boundary"] = "managed"
            elif name in excluded_set:
                ctx["agent_boundary"] = "excluded"

            # Management IP
            if name in mgmt_ips:
                ctx["mgmt_ip"] = mgmt_ips[name]

            # BGP config from spec ASN
            if "asn" in dev:
                bgp_cfg: dict = {"asn": dev["asn"]}
                # Spines are route reflectors
                if role == "spine":
                    bgp_cfg["role"] = "rr"
                elif role == "leaf":
                    bgp_cfg["role"] = "client"
                ctx["bgp_config"] = bgp_cfg

            # EVPN overlay (for fabric sites)
            if spec_key in FABRIC_SITES and overlay.get("neighbors"):
                if role == "spine":
                    ctx["evpn_overlay"] = {
                        "role": "route-reflector",
                        "neighbors": overlay["neighbors"],
                    }
                elif role == "leaf":
                    # Check if collapsed design (no spines in site)
                    spines = [d for d in site_data.get("devices", []) if d["role"] == "spine"]
                    if spines:
                        # Standard design — leaves peer with spines
                        pass
                    else:
                        # Collapsed design — leaves peer with each other
                        ctx["evpn_overlay"] = {
                            "role": "peer",
                            "asn": shared_overlay_asn,
                            "neighbors": overlay["neighbors"],
                        }

            # VXLAN config (for leaves with VNI mappings)
            if vxlan and role == "leaf":
                vtep_sources = vxlan.get("vtep_sources", {})
                vtep_ip = vtep_sources.get(name, "")
                if vtep_ip:
                    vni_mappings = vxlan.get("vni_mappings", [])
                    ctx["vxlan_config"] = {
                        "vtep_source_interface": "Loopback1",
                        "vtep_ip": vtep_ip,
                        "vni_mappings": vni_mappings,
                    }

            # Observed interfaces (borders and leaves facing firewalls)
            observed_list = boundary.get("observed", [])
            device_observed = [
                obs.split(":")[1] for obs in observed_list if obs.startswith(f"{name}:")
            ]
            if device_observed:
                ctx["observed_interfaces"] = device_observed

            # Thresholds + compliance on managed devices
            if name in managed_set:
                if thresholds:
                    ctx["agent_thresholds"] = thresholds
                if compliance_rules:
                    ctx["compliance_rules"] = compliance_rules

            if ctx:
                _update_context(nb, name, ctx)
                print(f"  {name}: {list(ctx.keys())}")

    # ------------------------------------------------------------------
    # 2. Process WAN transport devices
    # ------------------------------------------------------------------
    for dev in spec.get("wan_transport", {}).get("devices", []):
        name = dev["name"]
        ctx: dict = {}

        if name in managed_set:
            ctx["agent_boundary"] = "managed"
        elif name in excluded_set:
            ctx["agent_boundary"] = "excluded"

        if name in mgmt_ips:
            ctx["mgmt_ip"] = mgmt_ips[name]

        if "asn" in dev:
            ctx["bgp_config"] = {"asn": dev["asn"]}

        if name in managed_set:
            if thresholds:
                ctx["agent_thresholds"] = thresholds
            if compliance_rules:
                ctx["compliance_rules"] = compliance_rules

        if ctx:
            _update_context(nb, name, ctx)
            print(f"  {name}: {list(ctx.keys())}")

    # ------------------------------------------------------------------
    # 3. Process security (firewall) devices
    # ------------------------------------------------------------------
    for zone_key, zone_data in spec.get("security", {}).items():
        zones = zone_data.get("zones", [])
        policies = zone_data.get("policies", [])
        firewalls = zone_data.get("firewalls", [])

        for i, dev in enumerate(firewalls):
            name = dev["name"]
            ctx: dict = {}

            if name in managed_set:
                ctx["agent_boundary"] = "managed"
            elif name in excluded_set:
                ctx["agent_boundary"] = "excluded"

            if name in mgmt_ips:
                ctx["mgmt_ip"] = mgmt_ips[name]

            # HA config — first firewall is active, second is standby
            ha_role = "active" if i == 0 else "standby"
            ha_peer = firewalls[1 - i]["name"] if len(firewalls) > 1 else ""
            ha_group_id = 1 if zone_key == "dc" else 2
            ha_priority = 200 if i == 0 else 150

            ctx["ha_config"] = {
                "role": ha_role,
                "peer": ha_peer,
                "group_id": ha_group_id,
                "priority": ha_priority,
            }

            # Security zones and policies from spec
            if zones:
                ctx["security_zones"] = zones
            if policies:
                ctx["security_policies"] = policies

            if name in managed_set:
                if thresholds:
                    ctx["agent_thresholds"] = thresholds
                if compliance_rules:
                    ctx["compliance_rules"] = compliance_rules

            if ctx:
                _update_context(nb, name, ctx)
                print(f"  {name}: {list(ctx.keys())}")

    print("\nAll config contexts populated from spec.")


def main() -> None:
    """CLI entrypoint."""
    from pathlib import Path

    import yaml

    spec_path = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
    if not spec_path.exists():
        print(f"ERROR: Spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(spec_path.read_text())
    creds = require_credentials("netbox_url", "netbox_token")

    print(f"Connecting to NetBox at {creds.netbox_url}...")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)
    print("Connected.\n")

    populate_contexts(nb, spec)


if __name__ == "__main__":
    main()
