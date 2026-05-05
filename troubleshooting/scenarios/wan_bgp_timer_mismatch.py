"""Scenario: dc-ce-1 ↔ sp-pe-1 BGP keepalive/hold timers are too aggressive.

Setting `neighbor 172.16.0.1 timers 5 15` on dc-ce-1 forces a 15-second
hold time. sp-pe-1 negotiates the lower of the two sides, but a 5/15 timer
on a virtual lab link with normal jitter regularly misses keepalives and
the session flaps. Classic "session keeps bouncing" pattern.
"""

from __future__ import annotations

import re

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-ce-1"
PE_PRIMARY = "172.16.0.1"
PE_LOCAL_ASN = 65100  # dc-ce-1's local AS
NORMAL_HOLD_TIME = 180


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {PE_LOCAL_ASN}",
            f"neighbor {PE_PRIMARY} timers 5 15",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {PE_LOCAL_ASN}",
            f"no neighbor {PE_PRIMARY} timers",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command(f"show ip bgp neighbors {PE_PRIMARY}")
    m = re.search(r"Configured hold time is (\d+)", raw)
    if not m:
        return False, "could not parse hold time"
    configured = int(m.group(1))
    if configured < NORMAL_HOLD_TIME:
        return True, (
            f"configured hold time on {PE_PRIMARY} is {configured}s "
            f"(normal is {NORMAL_HOLD_TIME}s) — session will flap"
        )
    return False, f"hold time on {PE_PRIMARY} is {configured}s — within normal range"


SCENARIO = Scenario(
    name="wan-bgp-timer-mismatch",
    device=DEVICE,
    platform="cisco_iosxe",
    difficulty="intermediate",
    summary="dc-ce-1 BGP timers to sp-pe-1 are too aggressive — session flaps.",
    symptoms=(
        "BGP session to sp-pe-1 keeps going Idle and re-establishing every "
        "minute or two. Routes flap, traffic intermittently fails over to "
        "sp-pe-2 then comes back. sp-pe-2 session is rock-solid."
    ),
    runbook="troubleshooting/runbooks/wan_bgp_timer_mismatch.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
