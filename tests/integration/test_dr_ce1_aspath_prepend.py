"""TDD example: dr-ce-1 prepends its AS twice on egress to sp-pe-2.

Outbound TE: make the path via sp-pe-2 *look longer* to anyone selecting
on AS-path length. Verifiable on sp-pe-2's BGP table.

NOTE on a subtle real-world behavior this exercise also surfaces:
  Because sp-pe-1 and sp-pe-2 run iBGP between themselves, sp-pe-2's RIB
  has two paths for 172.16.0.102:
    - direct eBGP from dr-ce-1 with the prepended AS-path (length 3)
    - iBGP from sp-pe-1 with the unprepended AS-path (length 1)
  BGP best-path picks the shorter iBGP version, so dc-ce-1 ends up
  receiving identical AS-path lengths from both PEs — the prepend is
  *masked* by the SP's internal iBGP. This is a real production gotcha
  that pure AS-path prepending can't solve in single-SP topologies; the
  proper tools there are MED, communities + LOCAL_PREF on the SP side,
  or different-AS multi-homing.

What this test verifies:
  - The outbound route-map is in place on dr-ce-1 (config-side correctness)
  - sp-pe-2 receives the prepended path on its eBGP from dr-ce-1
"""

from __future__ import annotations

import re

import pytest
from nornir_netmiko.tasks import netmiko_send_command

from automation.inventory import init_nornir

pytestmark = pytest.mark.integration

DR_CE_1_AS = 65130
DR_CE_1_LOOPBACK = "172.16.0.102"
DR_CE_1_TO_SP_PE_2_IP = "172.16.0.13"  # dr-ce-1's Gi4 (eBGP peer of sp-pe-2's Gi4)


@pytest.fixture(scope="module")
def sp_pe_2_view() -> str:
    """Show the sp-pe-2 RIB entry for dr-ce-1's loopback."""
    nr = init_nornir(role=None)  # role=None to include excluded SP devices
    target = nr.filter(filter_func=lambda h: h.name == "sp-pe-2")
    if not target.inventory.hosts:
        pytest.skip("sp-pe-2 not in inventory")
    result = target.run(task=netmiko_send_command, command_string=f"show ip bgp {DR_CE_1_LOOPBACK}")
    if result["sp-pe-2"].failed:
        pytest.fail(f"sp-pe-2 fetch failed: {result['sp-pe-2'].exception}")
    return str(result["sp-pe-2"][0].result)


def _stanza_from_peer(raw: str, peer_ip: str) -> str | None:
    """Find the path stanza in show ip bgp output advertised by `peer_ip`."""
    stanzas = re.split(r"\n\s*Refresh Epoch \d+\n", raw)
    for s in stanzas:
        if f"from {peer_ip}" in s:
            return s
    return None


def _aspath_length(stanza: str) -> int:
    """Count AS-path tokens in a Cisco show-ip-bgp path stanza.

    The AS-path is the line above '<next-hop> from <peer-ip>'; tokens are
    integers separated by spaces, terminated by an origin code or end-of-line.
    """
    lines = stanza.split("\n")
    for i, line in enumerate(lines):
        if "from" in line and re.search(r"\d+\.\d+\.\d+\.\d+\s+from\s+\d+", line):
            # AS-path is the previous non-empty line
            for j in range(i - 1, -1, -1):
                prev = lines[j].strip()
                if prev:
                    tokens = re.findall(r"\b\d+\b", prev)
                    return len(tokens)
    return 0


class TestDrCe1Prepending:
    """dr-ce-1 prepends its AS twice on egress to sp-pe-2."""

    def test_sp_pe_2_received_prepended_path_from_dr_ce_1(self, sp_pe_2_view):
        """sp-pe-2's eBGP path from dr-ce-1 must show 3x AS 65130."""
        stanza = _stanza_from_peer(sp_pe_2_view, DR_CE_1_TO_SP_PE_2_IP)
        assert stanza, f"no path from {DR_CE_1_TO_SP_PE_2_IP} (dr-ce-1) found in sp-pe-2's RIB"
        as_count = _aspath_length(stanza)
        assert as_count == 3, (
            f"expected AS-path length 3 (prepended 2x by dr-ce-1), got {as_count}\n"
            f"stanza was:\n{stanza}"
        )

    def test_aspath_contains_correct_asn(self, sp_pe_2_view):
        """The prepend should be the local AS (65130), not some other value."""
        stanza = _stanza_from_peer(sp_pe_2_view, DR_CE_1_TO_SP_PE_2_IP)
        # The AS-path line should have three 65130 tokens
        lines = stanza.split("\n") if stanza else []
        for i, line in enumerate(lines):
            if "from" in line and re.search(r"\d+\.\d+\.\d+\.\d+\s+from", line):
                for j in range(i - 1, -1, -1):
                    if lines[j].strip():
                        tokens = re.findall(r"\b\d+\b", lines[j].strip())
                        assert tokens.count(str(DR_CE_1_AS)) == 3, (
                            f"expected 3x {DR_CE_1_AS} in AS-path, got tokens={tokens}"
                        )
                        return
        pytest.fail("could not parse AS-path line")
