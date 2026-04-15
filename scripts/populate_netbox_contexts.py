"""Populate NetBox config contexts with full fabric, VXLAN, security, and HA data.

This is the missing piece that makes the generator pull EVERYTHING from NetBox.
After running this, `make generate-spec` produces a complete YAML spec with no
hand-written data.

Usage:
    python -m scripts.populate_netbox_contexts
"""

from __future__ import annotations

import pynetbox

from scripts.bootstrap_config import get_mgmt_ips, get_shared_overlay_asn
from scripts.credentials import require_credentials

# Loaded from configs/lab_bootstrap.yaml — no hardcoded values.
MGMT_IPS = get_mgmt_ips()
SHARED_OVERLAY_ASN = get_shared_overlay_asn()


def _update_context(nb: pynetbox.api, device_name: str, context: dict) -> None:
    """Merge context data into a device's local_context_data."""
    dev = nb.dcim.devices.get(name=device_name)
    if not dev:
        print(f"  WARNING: Device {device_name} not found")
        return
    existing = dev.local_context_data or {}
    existing.update(context)
    dev.local_context_data = existing
    dev.save()


def populate_contexts(nb: pynetbox.api) -> None:
    """Push all config context data into NetBox devices."""

    # ==================================================================
    # 1. DC-East Spines — BGP RR, EVPN overlay config
    # ==================================================================
    print("DC-East Spines")
    spine_overlay = {
        "bgp_config": {"asn": 65000, "role": "rr"},
        "agent_boundary": "managed",
        "evpn_overlay": {
            "role": "route-reflector",
            "neighbors": [
                {"address": "10.1.0.11", "remote_as": 65000, "description": "dc-leaf-1 EVPN"},
                {"address": "10.1.0.12", "remote_as": 65000, "description": "dc-leaf-2 EVPN"},
                {"address": "10.1.0.13", "remote_as": 65000, "description": "dc-border-1 EVPN"},
                {"address": "10.1.0.14", "remote_as": 65000, "description": "dc-border-2 EVPN"},
            ],
        },
        "mgmt_ip": MGMT_IPS["dc-spine-1"],
    }
    _update_context(nb, "dc-spine-1", spine_overlay)

    spine2_overlay = dict(spine_overlay)
    spine2_overlay["mgmt_ip"] = MGMT_IPS["dc-spine-2"]
    _update_context(nb, "dc-spine-2", spine2_overlay)
    print("  dc-spine-1, dc-spine-2 — overlay RR + EVPN neighbors")

    # ==================================================================
    # 2. DC-East Compute Leaves — VXLAN, VTEP, anycast gateway
    # ==================================================================
    print("DC-East Compute Leaves")
    _update_context(
        nb,
        "dc-leaf-1",
        {
            "bgp_config": {"asn": 65001, "role": "client"},
            "agent_boundary": "managed",
            "vxlan_config": {
                "vtep_source_interface": "Loopback1",
                "vtep_ip": "10.1.2.11/32",
                "vni_mappings": [
                    {
                        "vni": 10100,
                        "vlan": 100,
                        "name": "SERVERS_A",
                        "subnet": "10.10.1.0/24",
                        "gateway": "10.10.1.1/24",
                    },
                ],
            },
            "mgmt_ip": MGMT_IPS["dc-leaf-1"],
        },
    )

    _update_context(
        nb,
        "dc-leaf-2",
        {
            "bgp_config": {"asn": 65002, "role": "client"},
            "agent_boundary": "managed",
            "vxlan_config": {
                "vtep_source_interface": "Loopback1",
                "vtep_ip": "10.1.2.12/32",
                "vni_mappings": [
                    {
                        "vni": 10200,
                        "vlan": 200,
                        "name": "SERVERS_B",
                        "subnet": "10.10.2.0/24",
                        "gateway": "10.10.2.1/24",
                    },
                ],
            },
            "mgmt_ip": MGMT_IPS["dc-leaf-2"],
        },
    )
    print("  dc-leaf-1 (VNI 10100), dc-leaf-2 (VNI 10200)")

    # ==================================================================
    # 3. DC-East Border Leaves
    # ==================================================================
    print("DC-East Border Leaves")
    _update_context(
        nb,
        "dc-border-1",
        {
            "bgp_config": {"asn": 65003},
            "agent_boundary": "managed",
            "observed_interfaces": ["Ethernet3"],
            "mgmt_ip": MGMT_IPS["dc-border-1"],
        },
    )
    _update_context(
        nb,
        "dc-border-2",
        {
            "bgp_config": {"asn": 65004},
            "agent_boundary": "managed",
            "observed_interfaces": ["Ethernet3"],
            "mgmt_ip": MGMT_IPS["dc-border-2"],
        },
    )
    print("  dc-border-1 (AS 65003), dc-border-2 (AS 65004)")

    # ==================================================================
    # 4. DC-East Firewalls — HA config, security zones, policies
    # ==================================================================
    print("DC Security (FortiGate HA)")
    _update_context(
        nb,
        "dc-fw-1",
        {
            "agent_boundary": "excluded",
            "ha_config": {"role": "active", "peer": "dc-fw-2", "group_id": 1, "priority": 200},
            "security_zones": [
                {"name": "dc-trust", "type": "trust", "interfaces": ["port1"]},
                {"name": "wan-untrust", "type": "untrust", "interfaces": ["port2"]},
            ],
            "security_policies": [
                {
                    "name": "allow-dc-outbound",
                    "source_zone": "dc-trust",
                    "destination_zone": "wan-untrust",
                    "action": "allow",
                    "services": ["tcp/443", "tcp/80", "icmp"],
                },
                {
                    "name": "allow-dc-ssh",
                    "source_zone": "dc-trust",
                    "destination_zone": "wan-untrust",
                    "action": "allow",
                    "services": ["tcp/22"],
                },
            ],
            "mgmt_ip": MGMT_IPS["dc-fw-1"],
        },
    )
    _update_context(
        nb,
        "dc-fw-2",
        {
            "agent_boundary": "excluded",
            "ha_config": {"role": "standby", "peer": "dc-fw-1", "group_id": 1, "priority": 150},
            "security_zones": [
                {"name": "dc-trust", "type": "trust", "interfaces": ["port1"]},
                {"name": "wan-untrust", "type": "untrust", "interfaces": ["port2"]},
            ],
            "security_policies": [
                {
                    "name": "allow-dc-outbound",
                    "source_zone": "dc-trust",
                    "destination_zone": "wan-untrust",
                    "action": "allow",
                    "services": ["tcp/443", "tcp/80", "icmp"],
                },
                {
                    "name": "allow-dc-ssh",
                    "source_zone": "dc-trust",
                    "destination_zone": "wan-untrust",
                    "action": "allow",
                    "services": ["tcp/22"],
                },
            ],
            "mgmt_ip": MGMT_IPS["dc-fw-2"],
        },
    )
    print("  dc-fw-1 (active), dc-fw-2 (standby)")

    # ==================================================================
    # 5. WAN Transport — CE and PE routers
    # ==================================================================
    print("WAN Transport")
    _update_context(
        nb,
        "dc-ce-1",
        {
            "bgp_config": {"asn": 65100},
            "agent_boundary": "excluded",
            "mgmt_ip": MGMT_IPS["dc-ce-1"],
        },
    )
    _update_context(
        nb,
        "sp-pe-1",
        {
            "bgp_config": {"asn": 64500},
            "agent_boundary": "excluded",
            "mgmt_ip": MGMT_IPS["sp-pe-1"],
        },
    )
    _update_context(
        nb,
        "sp-pe-2",
        {
            "bgp_config": {"asn": 64500},
            "agent_boundary": "excluded",
            "mgmt_ip": MGMT_IPS["sp-pe-2"],
        },
    )
    _update_context(
        nb,
        "dr-ce-1",
        {
            "bgp_config": {"asn": 65100},
            "agent_boundary": "excluded",
            "mgmt_ip": MGMT_IPS["dr-ce-1"],
        },
    )
    print("  dc-ce-1, sp-pe-1, sp-pe-2, dr-ce-1")

    # ==================================================================
    # 6. Branch-01
    # ==================================================================
    print("Branch-01")
    _update_context(
        nb,
        "br-ce-1",
        {
            "bgp_config": {"asn": 65100},
            "agent_boundary": "managed",
            "mgmt_ip": MGMT_IPS["br-ce-1"],
        },
    )
    _update_context(
        nb,
        "br-host-1",
        {
            "agent_boundary": "excluded",
            "mgmt_ip": MGMT_IPS["br-host-1"],
        },
    )
    print("  br-ce-1 (managed), br-host-1")

    # ==================================================================
    # 7. DR-West Leaves — collapsed EVPN (no spines)
    # ==================================================================
    print("DR-West Leaves")
    _update_context(
        nb,
        "dr-leaf-1",
        {
            "bgp_config": {"asn": 65201, "role": "client"},
            "agent_boundary": "managed",
            "observed_interfaces": ["Ethernet2"],
            "vxlan_config": {
                "vtep_source_interface": "Loopback1",
                "vtep_ip": "10.31.2.1/32",
                "vni_mappings": [
                    {
                        "vni": 10100,
                        "vlan": 100,
                        "name": "SERVERS_DR",
                        "subnet": "10.30.1.0/24",
                        "gateway": "10.30.1.1/24",
                    },
                ],
            },
            "evpn_overlay": {
                "role": "peer",
                "asn": 65201,
                "neighbors": [
                    {"address": "10.31.0.2", "remote_as": 65201, "description": "dr-leaf-2 EVPN"},
                ],
            },
            "mgmt_ip": MGMT_IPS["dr-leaf-1"],
        },
    )
    _update_context(
        nb,
        "dr-leaf-2",
        {
            "bgp_config": {"asn": 65202, "role": "client"},
            "agent_boundary": "managed",
            "observed_interfaces": ["Ethernet2"],
            "vxlan_config": {
                "vtep_source_interface": "Loopback1",
                "vtep_ip": "10.31.2.2/32",
                "vni_mappings": [
                    {
                        "vni": 10100,
                        "vlan": 100,
                        "name": "SERVERS_DR",
                        "subnet": "10.30.1.0/24",
                        "gateway": "10.30.1.1/24",
                    },
                ],
            },
            "evpn_overlay": {
                "role": "peer",
                "asn": 65201,
                "neighbors": [
                    {"address": "10.31.0.1", "remote_as": 65201, "description": "dr-leaf-1 EVPN"},
                ],
            },
            "mgmt_ip": MGMT_IPS["dr-leaf-2"],
        },
    )
    print("  dr-leaf-1 (AS 65201), dr-leaf-2 (AS 65202) — collapsed EVPN peers")

    # ==================================================================
    # 8. DR-West Firewalls
    # ==================================================================
    print("DR Security (FortiGate HA)")
    _update_context(
        nb,
        "dr-fw-1",
        {
            "agent_boundary": "excluded",
            "ha_config": {"role": "active", "peer": "dr-fw-2", "group_id": 2, "priority": 200},
            "security_zones": [
                {"name": "dr-trust", "type": "trust", "interfaces": ["port1"]},
                {"name": "dr-untrust", "type": "untrust", "interfaces": ["port2"]},
            ],
            "security_policies": [
                {
                    "name": "allow-dr-outbound",
                    "source_zone": "dr-trust",
                    "destination_zone": "dr-untrust",
                    "action": "allow",
                    "services": ["tcp/443", "tcp/80", "icmp"],
                },
            ],
            "mgmt_ip": MGMT_IPS["dr-fw-1"],
        },
    )
    _update_context(
        nb,
        "dr-fw-2",
        {
            "agent_boundary": "excluded",
            "ha_config": {"role": "standby", "peer": "dr-fw-1", "group_id": 2, "priority": 150},
            "security_zones": [
                {"name": "dr-trust", "type": "trust", "interfaces": ["port1"]},
                {"name": "dr-untrust", "type": "untrust", "interfaces": ["port2"]},
            ],
            "security_policies": [
                {
                    "name": "allow-dr-outbound",
                    "source_zone": "dr-trust",
                    "destination_zone": "dr-untrust",
                    "action": "allow",
                    "services": ["tcp/443", "tcp/80", "icmp"],
                },
            ],
            "mgmt_ip": MGMT_IPS["dr-fw-2"],
        },
    )
    print("  dr-fw-1 (active), dr-fw-2 (standby)")

    # ==================================================================
    # 9. Hosts
    # ==================================================================
    print("Hosts")
    for name in ["dc-host-1", "dc-host-2", "dr-host-1"]:
        _update_context(
            nb,
            name,
            {
                "agent_boundary": "excluded",
                "mgmt_ip": MGMT_IPS[name],
            },
        )
    print("  dc-host-1, dc-host-2, dr-host-1")

    # ==================================================================
    # 10. Agent thresholds (global config context)
    # ==================================================================
    print("Agent Thresholds")
    # Store on spine-1 as the "anchor" device for global config
    _update_context(
        nb,
        "dc-spine-1",
        {
            "agent_thresholds": {
                "bgp_hold_time": 180,
                "interface_flap_window_sec": 300,
                "max_route_deviation_pct": 10.0,
            },
            "compliance_rules": [
                {
                    "name": "bgp-session-established",
                    "check": "all managed BGP sessions must be in Established state",
                    "severity": "critical",
                },
                {
                    "name": "vtep-reachability",
                    "check": "all VTEP loopbacks must be reachable from all other VTEPs",
                    "severity": "critical",
                },
                {
                    "name": "vni-consistency",
                    "check": "VNI-to-VLAN mappings must match across all sites",
                    "severity": "warning",
                },
            ],
        },
    )
    print("  Stored on dc-spine-1 (anchor device)")

    print("\nAll config contexts populated.")


def main() -> None:
    """CLI entrypoint."""
    creds = require_credentials("netbox_url", "netbox_token")
    print(f"Connecting to NetBox at {creds.netbox_url}...")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)
    print("Connected.\n")
    populate_contexts(nb)


if __name__ == "__main__":
    main()
