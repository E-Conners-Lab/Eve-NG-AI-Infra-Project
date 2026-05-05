"""Scenario: dc-leaf-2 EVPN VLAN 200 imports the wrong route-target.

Fabric standard is `65000:10200` for both import and export on VLAN 200.
This fault changes the *import* RT to `65000:99200` while keeping export
unchanged. Type-2 routes for VNI 10200 arrive on dc-leaf-2 (the BGP RIB
has them) but the L2 RIB doesn't import them — the EVI doesn't accept
routes that don't carry a matching import RT.

Symptom: dc-host-2 can ARP and reach dc-host-1 — its export RT is correct,
so its routes still propagate outward. But dc-leaf-2 never learns
dc-host-1's MAC over EVPN, so frames toward dc-host-1's MAC get flooded
(if BUM forwarding is set up) or dropped.
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-leaf-2"
LEAF_LOCAL_ASN = 65002
EXPECTED_RT = "65000:10200"
WRONG_RT = "65000:99200"
VLAN = 200


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {LEAF_LOCAL_ASN}",
            f"vlan {VLAN}",
            f"no route-target import {EXPECTED_RT}",
            f"route-target import {WRONG_RT}",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {LEAF_LOCAL_ASN}",
            f"vlan {VLAN}",
            f"no route-target import {WRONG_RT}",
            f"route-target import {EXPECTED_RT}",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show running-config | section router bgp")
    in_vlan_block = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"vlan {VLAN}"):
            in_vlan_block = True
            continue
        if in_vlan_block:
            if stripped.startswith("vlan ") or stripped.startswith("router bgp"):
                in_vlan_block = False
                continue
            if stripped.startswith("route-target import"):
                if EXPECTED_RT in stripped:
                    return False, f"VLAN {VLAN} import RT is {EXPECTED_RT}"
                return True, f"VLAN {VLAN} import RT is {stripped} — expected {EXPECTED_RT}"
    return True, f"VLAN {VLAN} block missing route-target import line"


SCENARIO = Scenario(
    name="evpn-wrong-rt-import",
    device=DEVICE,
    platform="arista_eos",
    difficulty="advanced",
    summary="dc-leaf-2 EVPN VLAN 200 import RT is wrong — Type-2 routes are filtered.",
    symptoms=(
        "dc-host-1 → dc-host-2 works. dc-host-2 → dc-host-1 hits unicast flooding "
        "or drops. `show bgp evpn` on dc-leaf-2 has the routes for dc-host-1's MAC, "
        "but `show mac address-table` does not. Local L2 within dc-host-2's VLAN works."
    ),
    runbook="troubleshooting/runbooks/evpn_wrong_rt_import.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
