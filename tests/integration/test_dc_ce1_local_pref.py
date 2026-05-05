"""TDD example: dc-ce-1 must prefer sp-pe-1 via BGP local-preference.

Asserts the outbound TE policy is in effect:
  - Routes received from sp-pe-1 (172.16.0.1) carry local-pref 200
  - Routes received from sp-pe-2 (172.16.0.3) carry local-pref 100
  - Best path is chosen by local-pref (deterministic), not router-id (incidental)

Marker: integration — run only against the live lab.
    .venv/bin/python -m pytest tests/integration/test_dc_ce1_local_pref.py -v -m integration
"""

from __future__ import annotations

import re

import pytest
from nornir_netmiko.tasks import netmiko_send_command

from automation.inventory import init_nornir

pytestmark = pytest.mark.integration

PROBE_PREFIX = "10.20.0.1"  # br-ce-1 loopback — visible to dc-ce-1 via both PEs
PE_PRIMARY = "172.16.0.1"
PE_SECONDARY = "172.16.0.3"
EXPECTED_PRIMARY_LOCALPREF = 200
EXPECTED_SECONDARY_LOCALPREF = 100


@pytest.fixture(scope="module")
def bgp_detail() -> str:
    """Run `show ip bgp <prefix>` on dc-ce-1 once for all tests in this module."""
    nr = init_nornir(role="managed")
    target = nr.filter(filter_func=lambda h: h.name == "dc-ce-1")
    result = target.run(task=netmiko_send_command, command_string=f"show ip bgp {PROBE_PREFIX}")
    if result["dc-ce-1"].failed:
        pytest.fail(f"could not fetch BGP detail: {result['dc-ce-1'].exception}")
    return str(result["dc-ce-1"][0].result)


def _localpref_for_path_from(raw: str, peer_ip: str) -> int | None:
    """Extract the local-pref value from the path stanza received from `peer_ip`.

    Cisco IOS-XE format: each path is preceded by 'Refresh Epoch N' and contains
    'X.X.X.X from Y.Y.Y.Y' where Y is the BGP peer that advertised it.
    """
    stanzas = re.split(r"\n\s*Refresh Epoch \d+\n", raw)
    for s in stanzas:
        if f"from {peer_ip}" in s:
            m = re.search(r"localpref\s+(\d+)", s)
            return int(m.group(1)) if m else None
    return None


def _best_path_next_hop(raw: str) -> str | None:
    """Return the next-hop IP of the path marked 'best' in show ip bgp output."""
    stanzas = re.split(r"\n\s*Refresh Epoch \d+\n", raw)
    for s in stanzas:
        if "best" in s:
            m = re.search(r"^\s+(\d+\.\d+\.\d+\.\d+) from", s, re.MULTILINE)
            if m:
                return m.group(1)
    return None


class TestPrimaryPathLocalPref:
    """Routes via sp-pe-1 must be elevated to local-pref 200."""

    def test_primary_pe_localpref_is_elevated(self, bgp_detail):
        actual = _localpref_for_path_from(bgp_detail, PE_PRIMARY)
        assert actual == EXPECTED_PRIMARY_LOCALPREF, (
            f"expected local-pref {EXPECTED_PRIMARY_LOCALPREF} on path from "
            f"sp-pe-1 ({PE_PRIMARY}), got {actual}"
        )


class TestSecondaryPathLocalPref:
    """Routes via sp-pe-2 stay at the default 100."""

    def test_secondary_pe_localpref_is_default(self, bgp_detail):
        actual = _localpref_for_path_from(bgp_detail, PE_SECONDARY)
        assert actual == EXPECTED_SECONDARY_LOCALPREF, (
            f"expected local-pref {EXPECTED_SECONDARY_LOCALPREF} on path from "
            f"sp-pe-2 ({PE_SECONDARY}), got {actual}"
        )


class TestBestPathDeterminedByLocalPref:
    """Best path must be the one with higher local-pref (sp-pe-1)."""

    def test_best_path_is_via_primary_pe(self, bgp_detail):
        actual_nh = _best_path_next_hop(bgp_detail)
        assert actual_nh == PE_PRIMARY, (
            f"expected best-path next-hop {PE_PRIMARY} (sp-pe-1), got {actual_nh}"
        )

    def test_path_diff_is_at_least_100(self, bgp_detail):
        """Sanity: the policy creates a meaningful gap between primary/secondary."""
        primary = _localpref_for_path_from(bgp_detail, PE_PRIMARY) or 0
        secondary = _localpref_for_path_from(bgp_detail, PE_SECONDARY) or 0
        assert primary - secondary >= 100, (
            f"local-pref gap too small: primary={primary} secondary={secondary}"
        )
