"""Scenario: Ethernet1 on dc-border-1 is administratively down.

Symptoms:
    - dc-border-1 loses one of its two spine uplinks
    - BGP session to dc-spine-1 over Ethernet1 goes Idle
    - Northbound traffic still flows via dc-spine-2 (asymmetric ECMP loss)

Used as the "hello world" of the framework — easiest to diagnose, exercises
the full inject/detect/fix loop end to end.
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-border-1"
INTERFACE = "Ethernet1"


def inject(conn: object) -> None:
    conn.send_config_set([f"interface {INTERFACE}", "shutdown"])


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command(f"show interfaces {INTERFACE} status")
    for line in raw.splitlines():
        if line.startswith(("Et1", "Ethernet1")):
            if "disabled" in line.lower():
                return True, f"{INTERFACE} is administratively disabled"
            if "connected" in line.lower():
                return False, f"{INTERFACE} is connected"
            return False, line.strip()
    return False, "interface not found in show output"


def fix(conn: object) -> None:
    conn.send_config_set([f"interface {INTERFACE}", "no shutdown"])


SCENARIO = Scenario(
    name="l1-iface-admin-down",
    device=DEVICE,
    platform="arista_eos",
    difficulty="beginner",
    summary="An uplink interface on dc-border-1 has been admin-shut.",
    symptoms=(
        "dc-border-1 has lost reachability over one of its two spine uplinks. "
        "BGP session over the affected link is down; the second uplink still works."
    ),
    runbook="troubleshooting/runbooks/l1_iface_admin_down.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
