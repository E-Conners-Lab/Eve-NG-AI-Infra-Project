"""Composite scenario: local-pref reversed AND a loud uplink flap.

The user-reported symptom is "DC outbound traffic is exiting via the
wrong PE." A junior operator will:

1. Open dc-ce-1, see the BGP table is choosing the wrong PE.
2. Open dc-border-1 first (because the BGP summary may show a flap there
   or someone in chat says "is the spine link down?").
3. See `interface Ethernet1` admin-down on dc-border-1.
4. Bring it back up. Convince themselves they fixed the problem.
5. The original symptom is *still* there because that wasn't the cause.

The interface flap is the red herring; the local-pref inversion is the
real cause. The skill being tested is to keep going past the first
fixable thing you find.

The CLI dispatches with a connection to the *primary* device (dc-ce-1).
This module opens a side connection to dc-border-1 for the secondary
fault.
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario, connect_device
from troubleshooting.scenarios import l1_iface_admin_down, wan_localpref_reversed

PRIMARY_DEVICE = "dc-ce-1"
SECONDARY_DEVICE = "dc-border-1"
SECONDARY_PLATFORM = "arista_eos"


def _with_secondary(callback) -> None:
    conn = connect_device(SECONDARY_DEVICE, platform=SECONDARY_PLATFORM)
    try:
        callback(conn)
    finally:
        conn.disconnect()


def inject(conn: object) -> None:
    wan_localpref_reversed.inject(conn)
    _with_secondary(l1_iface_admin_down.inject)


def fix(conn: object) -> None:
    wan_localpref_reversed.fix(conn)
    _with_secondary(l1_iface_admin_down.fix)


def detect(conn: object) -> tuple[bool, str]:
    lp_present, lp_evidence = wan_localpref_reversed.detect(conn)

    iface_present = False
    iface_evidence = ""

    def _check(c):
        nonlocal iface_present, iface_evidence
        iface_present, iface_evidence = l1_iface_admin_down.detect(c)

    _with_secondary(_check)

    if lp_present and iface_present:
        return True, f"BOTH faults present — localpref: {lp_evidence}; iface: {iface_evidence}"
    if lp_present:
        return True, f"localpref fault still present: {lp_evidence}"
    if iface_present:
        return True, f"iface fault still present: {iface_evidence}"
    return False, "both faults cleared"


SCENARIO = Scenario(
    name="multi-fault-localpref-and-iface",
    device=PRIMARY_DEVICE,
    platform="cisco_iosxe",
    difficulty="advanced",
    summary=(
        "Two faults at once: dc-ce-1 local-pref policy is inverted AND "
        "dc-border-1 has an admin-shut uplink. The uplink is the red herring."
    ),
    symptoms=(
        "User-reported symptom: DC outbound traffic is exiting via sp-pe-2 "
        "instead of sp-pe-1. While investigating, you'll also notice that "
        "dc-border-1 has lost one of its spine uplinks. Be careful: fixing "
        "the uplink does not fix the user-reported symptom."
    ),
    runbook="troubleshooting/runbooks/multi_fault_localpref_and_iface.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
