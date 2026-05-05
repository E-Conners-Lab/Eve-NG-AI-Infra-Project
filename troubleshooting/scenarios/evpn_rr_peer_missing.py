"""Scenario: dc-leaf-2's EVPN session is deactivated on dc-spine-1.

The underlay BGP session (10.1.0.12) is still Established. dc-leaf-2 still
*sends* its EVPN routes — but the spine no longer reflects them onward
because dc-leaf-2 isn't activated in the EVPN address-family. Other leaves
go blind to dc-host-2's MAC/IP.

Trap: `show ip bgp summary` looks completely healthy. You only see the
problem when you check `show bgp evpn summary` — a different command.
"""

from __future__ import annotations

import re

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-spine-1"
SPINE_LOCAL_ASN = 65000
LEAF_2_LOOPBACK = "10.1.0.12"


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {SPINE_LOCAL_ASN}",
            "address-family evpn",
            f"no neighbor {LEAF_2_LOOPBACK} activate",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {SPINE_LOCAL_ASN}",
            "address-family evpn",
            f"neighbor {LEAF_2_LOOPBACK} activate",
            f"neighbor {LEAF_2_LOOPBACK} next-hop-unchanged",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show bgp evpn summary")
    pattern = re.compile(rf"^{re.escape(LEAF_2_LOOPBACK)}\s", re.MULTILINE)
    if pattern.search(raw):
        return False, f"{LEAF_2_LOOPBACK} present in EVPN summary"
    return True, f"{LEAF_2_LOOPBACK} (dc-leaf-2) not active in EVPN address-family"


SCENARIO = Scenario(
    name="evpn-rr-peer-missing",
    device=DEVICE,
    platform="arista_eos",
    difficulty="advanced",
    summary="dc-leaf-2 EVPN session is not activated on dc-spine-1.",
    symptoms=(
        "Hosts on dc-leaf-2 are reachable from dc-leaf-2's own segment but "
        "not from dc-leaf-1 or the borders. `show ip bgp summary` looks "
        "perfect — all underlay sessions Established. The fault is invisible "
        "from the IPv4 unicast view."
    ),
    runbook="troubleshooting/runbooks/evpn_rr_peer_missing.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
