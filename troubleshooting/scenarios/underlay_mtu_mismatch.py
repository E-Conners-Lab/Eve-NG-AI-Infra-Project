"""Scenario: dc-leaf-1 Ethernet1 has MTU 1400 — VXLAN traffic blackholed.

Default underlay MTU on the spec is 9214 (jumbo) so VXLAN's 50-byte outer
header doesn't fragment user 1500-byte payloads. Setting MTU 1400 leaves
ICMP/SSH/BGP keepalives flowing — they're well under 1400 — while
VXLAN-encapped frames at full size silently drop. This is the canonical
PMTUD black hole pattern: control plane green, data plane bleeding.
"""

from __future__ import annotations

import re

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-leaf-1"
INTERFACE = "Ethernet1"
JUMBO_MTU = 9214
INJECTED_MTU = 1400


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"interface {INTERFACE}",
            f"mtu {INJECTED_MTU}",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"interface {INTERFACE}",
            f"mtu {JUMBO_MTU}",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command(f"show interfaces {INTERFACE}")
    m = re.search(r"IP MTU\s+(\d+)\s+bytes", raw)
    if not m:
        # Fall back to "MTU 9214 bytes" format some EOS versions use
        m = re.search(r"\bMTU\s+(\d+)\s+bytes", raw)
    if not m:
        return False, "could not parse MTU from interface output"
    mtu = int(m.group(1))
    if mtu < 1600:
        return True, f"{INTERFACE} MTU is {mtu} — too small for VXLAN-encapped traffic"
    return False, f"{INTERFACE} MTU is {mtu} — within VXLAN underlay range"


SCENARIO = Scenario(
    name="underlay-mtu-mismatch",
    device=DEVICE,
    platform="arista_eos",
    difficulty="advanced",
    summary="dc-leaf-1 Ethernet1 has MTU 1400 — VXLAN payloads silently dropped.",
    symptoms=(
        "Pings between dc-host-1 and dc-host-2 succeed at default 56-byte size. "
        "Pings with -s 1450 (or any large size) fail. Application traffic shows "
        "TCP retransmits and unexpected timeouts. BGP and EVPN sessions are all "
        "Established and stable — keepalives are tiny and pass through fine."
    ),
    runbook="troubleshooting/runbooks/underlay_mtu_mismatch.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
