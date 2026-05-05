"""Scenario: dc-leaf-1 anycast gateway MAC differs from the rest of the fabric.

Both leaves are configured as anycast gateways for the same SVIs (VLAN
100 and 200). For this to work, every leaf must advertise the *same*
virtual MAC for those SVIs — that's the whole point of anycast. Setting
a different value on dc-leaf-1 breaks consistency: hosts on dc-leaf-1
ARP for and cache one MAC; hosts on dc-leaf-2 cache another.

Symptoms are notoriously inconsistent — pings can work, then a host
moves between leaves and stops working until ARP expires.
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-leaf-1"
EXPECTED_MAC = "00:1c:73:00:00:01"
INJECTED_MAC = "00:1c:73:de:ad:01"


def inject(conn: object) -> None:
    conn.send_config_set([f"ip virtual-router mac-address {INJECTED_MAC}"])


def fix(conn: object) -> None:
    conn.send_config_set([f"ip virtual-router mac-address {EXPECTED_MAC}"])


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show ip virtual-router")
    raw_lower = raw.lower()
    if EXPECTED_MAC.lower() in raw_lower:
        return False, f"anycast MAC {EXPECTED_MAC} matches expected"
    return True, (
        f"anycast MAC on {DEVICE} does not match fabric value {EXPECTED_MAC} — "
        f"hosts behind this leaf will receive a different gateway MAC"
    )


SCENARIO = Scenario(
    name="evpn-anycast-gw-mac-mismatch",
    device=DEVICE,
    platform="arista_eos",
    difficulty="advanced",
    summary="dc-leaf-1 advertises a different anycast gateway MAC than dc-leaf-2.",
    symptoms=(
        "Connectivity works at first. After a host migrates between dc-leaf-1 "
        "and dc-leaf-2 (or after ARP entries expire and refresh), traffic to "
        "the gateway breaks for ~5 minutes until the new ARP cache populates. "
        "Bug is intermittent and hard to reproduce on demand."
    ),
    runbook="troubleshooting/runbooks/evpn_anycast_gw_mac_mismatch.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
