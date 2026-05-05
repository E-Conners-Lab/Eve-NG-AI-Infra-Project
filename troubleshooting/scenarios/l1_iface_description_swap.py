"""Scenario: dc-border-1 Et1/Et2 descriptions are swapped.

The physical wiring is unchanged: Ethernet1 is still the link to dc-spine-1
and Ethernet2 still goes to dc-spine-2. Only the *labels* are wrong. An
operator who trusts `show interfaces description` will troubleshoot the
wrong link and waste time.

Among the cheapest faults to inject and the most-cited reason in real
post-mortems for "we touched the wrong cable / interface."
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-border-1"
EXPECTED = {"Et1": "to dc-spine-1", "Et2": "to dc-spine-2"}


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            "interface Ethernet1",
            "description to dc-spine-2",
            "exit",
            "interface Ethernet2",
            "description to dc-spine-1",
            "exit",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            "interface Ethernet1",
            "description to dc-spine-1",
            "exit",
            "interface Ethernet2",
            "description to dc-spine-2",
            "exit",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show interfaces description")
    actual: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) >= 4 and parts[0] in EXPECTED:
            actual[parts[0]] = parts[3].strip()
    mismatches = [
        (iface, EXPECTED[iface], actual[iface])
        for iface in EXPECTED
        if iface in actual and actual[iface] != EXPECTED[iface]
    ]
    if mismatches:
        details = "; ".join(f"{i}: {a} (expected {e})" for i, e, a in mismatches)
        return True, f"description mismatch — {details}"
    return False, "Et1/Et2 descriptions match spec"


SCENARIO = Scenario(
    name="l1-iface-description-swap",
    device=DEVICE,
    platform="arista_eos",
    difficulty="beginner",
    summary="dc-border-1 Et1/Et2 descriptions are swapped — labels lie about wiring.",
    symptoms=(
        "An operator was asked to drain traffic from the dc-spine-2 uplink and "
        "shut what `show interfaces description` reported as that link. The "
        "wrong session went down. Wiring is unchanged; only the labels are wrong."
    ),
    runbook="troubleshooting/runbooks/l1_iface_description_swap.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
