"""Scenario: dc-leaf-2 is missing the vxlan vlan-to-VNI mapping for VNI 10200.

dc-host-2 lives on VLAN 200 / VNI 10200. With the mapping gone, frames from
the local host hit Vxlan1 with no VNI to encapsulate into — they're dropped
silently. The EVPN control plane (Type-2 MAC/IP routes) keeps advertising
the host MAC because the L2VPN EVI is still configured, so peers think the
host exists. Cross-fabric traffic blackholes one direction.
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-leaf-2"
VNI = 10200
VLAN = 200


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            "interface Vxlan1",
            f"no vxlan vlan {VLAN} vni {VNI}",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            "interface Vxlan1",
            f"vxlan vlan {VLAN} vni {VNI}",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show vxlan vni")
    vni_str = str(VNI)
    for line in raw.splitlines():
        # First whitespace-separated token is the VNI
        parts = line.split()
        if parts and parts[0] == vni_str:
            return False, f"VNI {VNI} mapping present"
    return True, f"VNI {VNI} (VLAN {VLAN}) not in vxlan mapping table"


SCENARIO = Scenario(
    name="evpn-vni-mapping-missing",
    device=DEVICE,
    platform="arista_eos",
    difficulty="advanced",
    summary="dc-leaf-2 is missing the VLAN 200 → VNI 10200 mapping on Vxlan1.",
    symptoms=(
        "dc-host-2 (10.10.2.10) is unreachable from dc-host-1 across the fabric. "
        "BGP EVPN routes for 10.10.2.10 are present in spines and remote leaves, "
        "but pings still fail. Local-leaf ARP for 10.10.2.10 succeeds — the "
        "problem only shows up when traffic crosses the VTEP boundary."
    ),
    runbook="troubleshooting/runbooks/evpn_vni_mapping_missing.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
