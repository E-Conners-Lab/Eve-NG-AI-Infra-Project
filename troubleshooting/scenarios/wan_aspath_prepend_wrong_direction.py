"""Scenario: dr-ce-1 AS-path prepend route-map is bound outbound to the wrong PE.

The intent is to make the *secondary* path artificially long so inbound
traffic from the SP picks the *primary* PE. Binding LONG-PATH-OUT to the
primary instead of the secondary inverts the policy: the secondary PE now
advertises a shorter path than the primary, and return traffic enters DR
via sp-pe-2.

This causes asymmetric routing: outbound TE policies (local-pref) still
prefer sp-pe-1, but inbound traffic comes back via sp-pe-2. Stateful
middleboxes (firewalls, IPS) on either path will start dropping flows.
"""

from __future__ import annotations

import re

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dr-ce-1"
PE_PRIMARY = "172.16.0.10"
PE_SECONDARY = "172.16.0.12"
DR_LOCAL_ASN = 65130
ROUTE_MAP = "LONG-PATH-OUT"


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {DR_LOCAL_ASN}",
            "address-family ipv4",
            f"no neighbor {PE_SECONDARY} route-map {ROUTE_MAP} out",
            f"neighbor {PE_PRIMARY} route-map {ROUTE_MAP} out",
            "exit-address-family",
        ]
    )
    conn.send_command(f"clear ip bgp {PE_PRIMARY} soft out", read_timeout=30)
    conn.send_command(f"clear ip bgp {PE_SECONDARY} soft out", read_timeout=30)


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {DR_LOCAL_ASN}",
            "address-family ipv4",
            f"no neighbor {PE_PRIMARY} route-map {ROUTE_MAP} out",
            f"neighbor {PE_SECONDARY} route-map {ROUTE_MAP} out",
            "exit-address-family",
        ]
    )
    conn.send_command(f"clear ip bgp {PE_PRIMARY} soft out", read_timeout=30)
    conn.send_command(f"clear ip bgp {PE_SECONDARY} soft out", read_timeout=30)


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show running-config | section router bgp")
    bound_to_primary = bool(
        re.search(rf"neighbor {re.escape(PE_PRIMARY)} route-map {ROUTE_MAP} out", raw)
    )
    bound_to_secondary = bool(
        re.search(rf"neighbor {re.escape(PE_SECONDARY)} route-map {ROUTE_MAP} out", raw)
    )
    if bound_to_primary and not bound_to_secondary:
        return True, (
            f"{ROUTE_MAP} is bound out toward primary {PE_PRIMARY} — "
            f"inbound traffic will prefer secondary"
        )
    if bound_to_secondary and not bound_to_primary:
        return False, f"{ROUTE_MAP} bound out toward secondary {PE_SECONDARY} — correct"
    return False, "ambiguous binding state — manual inspection recommended"


SCENARIO = Scenario(
    name="wan-aspath-prepend-wrong-direction",
    device=DEVICE,
    platform="cisco_iosxe",
    difficulty="intermediate",
    summary="dr-ce-1 LONG-PATH-OUT prepend route-map is bound to the wrong PE.",
    symptoms=(
        "Outbound traffic from DR exits via sp-pe-1 as expected, but return "
        "traffic comes back via sp-pe-2 — asymmetric. Stateful firewalls in "
        "the path complain about half-flows. Local-pref looks fine; the "
        "issue is the *advertised* AS-path length on the SP side."
    ),
    runbook="troubleshooting/runbooks/wan_aspath_prepend_wrong_direction.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
