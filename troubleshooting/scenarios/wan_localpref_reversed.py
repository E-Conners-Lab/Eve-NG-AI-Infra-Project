"""Scenario: dc-ce-1 PRIMARY-PE / SECONDARY-PE local-preference values swapped.

The route-map names still look correct — PRIMARY-PE is still bound to
sp-pe-1 inbound and SECONDARY-PE to sp-pe-2 — but the *values* set inside
each route-map have been inverted. Outbound DC traffic now exits via
sp-pe-2 instead of sp-pe-1.

This is a real-world classic: someone "fixed" the local-pref under pressure
and got the values backwards. Logging into the router and reading the
neighbor bindings won't reveal it; you have to read the route-map *bodies*
or look at the actual local-pref carried in the BGP table.
"""

from __future__ import annotations

import re

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-ce-1"
PROBE_PREFIX = "10.20.0.1"
PE_PRIMARY = "172.16.0.1"
PE_SECONDARY = "172.16.0.3"
EXPECTED_PRIMARY_LOCALPREF = 200


def _swap_route_map_values(conn, primary_lp: int, secondary_lp: int) -> None:
    """Set PRIMARY-PE -> primary_lp and SECONDARY-PE -> secondary_lp."""
    conn.send_config_set(
        [
            "route-map PRIMARY-PE permit 10",
            f"set local-preference {primary_lp}",
            "exit",
            "route-map SECONDARY-PE permit 10",
            f"set local-preference {secondary_lp}",
            "exit",
        ]
    )
    # Inbound soft-refresh on both PEs so the new values reach the BGP table
    conn.send_command(f"clear ip bgp {PE_PRIMARY} soft in", read_timeout=30)
    conn.send_command(f"clear ip bgp {PE_SECONDARY} soft in", read_timeout=30)


def inject(conn: object) -> None:
    _swap_route_map_values(conn, primary_lp=100, secondary_lp=200)


def fix(conn: object) -> None:
    _swap_route_map_values(conn, primary_lp=200, secondary_lp=100)


def _localpref_for_path_from(raw: str, peer_ip: str) -> int | None:
    """Parse `show ip bgp <prefix>` output for the local-pref of a peer's path.

    Cisco IOS-XE format: each path begins with 'Refresh Epoch N' and contains
    'X.X.X.X from Y.Y.Y.Y' where Y is the advertising peer. localpref appears
    on the next line.
    """
    stanzas = re.split(r"\n\s*Refresh Epoch \d+\n", raw)
    for s in stanzas:
        if f"from {peer_ip}" in s:
            m = re.search(r"localpref\s+(\d+)", s)
            return int(m.group(1)) if m else None
    return None


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command(f"show ip bgp {PROBE_PREFIX}")
    primary_lp = _localpref_for_path_from(raw, PE_PRIMARY)
    secondary_lp = _localpref_for_path_from(raw, PE_SECONDARY)
    if primary_lp is None or secondary_lp is None:
        return False, f"could not parse both paths (primary={primary_lp}, secondary={secondary_lp})"
    if primary_lp >= EXPECTED_PRIMARY_LOCALPREF and primary_lp > secondary_lp:
        return False, f"primary={primary_lp} secondary={secondary_lp} — policy intact"
    return True, (
        f"primary path from {PE_PRIMARY} has localpref {primary_lp}, "
        f"secondary from {PE_SECONDARY} has {secondary_lp} — values appear swapped"
    )


SCENARIO = Scenario(
    name="wan-localpref-reversed",
    device=DEVICE,
    platform="cisco_iosxe",
    difficulty="intermediate",
    summary="dc-ce-1 BGP local-preference policy is inverted between PEs.",
    symptoms=(
        "Outbound traffic from DC is exiting via sp-pe-2 instead of sp-pe-1. "
        "Both BGP sessions are Established and all routes are reachable, but "
        "the wrong PE is being preferred. Capture on either PE will confirm."
    ),
    runbook="troubleshooting/runbooks/wan_localpref_reversed.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
