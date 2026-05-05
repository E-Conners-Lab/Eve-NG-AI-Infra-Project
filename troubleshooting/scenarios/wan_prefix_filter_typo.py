"""Scenario: stray inbound prefix-list on dc-ce-1 denies 10.20.0.0/16 from sp-pe-1.

A real-world classic: a "temporary" filter from a change window left in the
config. Prefixes from the branch supernet are silently dropped on receive
from the primary PE, so the BGP table only shows the secondary path. Local-pref
no longer matters because there's only one path to choose from.
"""

from __future__ import annotations

import re

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-ce-1"
PE_PRIMARY = "172.16.0.1"
PE_LOCAL_ASN = 65100
PROBE_PREFIX = "10.20.0.1"
FILTER_NAME = "TS-DENY-BRANCH"


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"ip prefix-list {FILTER_NAME} seq 10 deny 10.20.0.0/16 le 32",
            f"ip prefix-list {FILTER_NAME} seq 20 permit 0.0.0.0/0 le 32",
            f"router bgp {PE_LOCAL_ASN}",
            f"neighbor {PE_PRIMARY} prefix-list {FILTER_NAME} in",
        ]
    )
    conn.send_command(f"clear ip bgp {PE_PRIMARY} soft in", read_timeout=30)


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {PE_LOCAL_ASN}",
            f"no neighbor {PE_PRIMARY} prefix-list {FILTER_NAME} in",
            "exit",
            f"no ip prefix-list {FILTER_NAME}",
        ]
    )
    conn.send_command(f"clear ip bgp {PE_PRIMARY} soft in", read_timeout=30)


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command(f"show ip bgp {PROBE_PREFIX}")
    if "Network not in table" in raw:
        return True, f"{PROBE_PREFIX} missing from BGP table entirely"
    has_primary = bool(re.search(rf"from {re.escape(PE_PRIMARY)}\b", raw))
    if not has_primary:
        return True, f"path via primary PE {PE_PRIMARY} missing — likely filtered inbound"
    return False, f"both PE paths present for {PROBE_PREFIX}"


SCENARIO = Scenario(
    name="wan-prefix-filter-typo",
    device=DEVICE,
    platform="cisco_iosxe",
    difficulty="intermediate",
    summary="An inbound prefix-list on dc-ce-1 denies 10.20.0.0/16 from sp-pe-1.",
    symptoms=(
        "Branch prefixes are reachable but only via sp-pe-2 — the primary "
        "path has silently disappeared. show ip bgp summary still shows both "
        "neighbors Established with similar prefix counts (off by one)."
    ),
    runbook="troubleshooting/runbooks/wan_prefix_filter_typo.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
