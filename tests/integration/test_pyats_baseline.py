"""Baseline pyATS-style integration test against the live lab.

This is the foundation of the TDD workflow: assert the lab is in a known-good
state. Future tests build on this by changing the spec, deploying, then
asserting the new expected state.

Marked `integration` — pytest skips by default. Run explicitly with:
    .venv/bin/python -m pytest tests/integration/test_pyats_baseline.py -v -m integration

Requirements:
- Live lab reachable from this host
- agent/testbed.yaml present (regenerate with `python -m scripts.generate_testbed`)
- DEVICE_USERNAME / DEVICE_PASSWORD set or in .env
"""

from __future__ import annotations

import pytest

from automation.bgp_state import parse_bgp_summary
from automation.inventory import init_nornir

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def nornir():
    """One Nornir instance per module — opens connections lazily on first run()."""
    return init_nornir(role="managed")


@pytest.fixture(scope="module")
def routing_devices(nornir):
    """Filter to Arista + Cisco devices that run BGP. Excludes FortiGate."""
    return nornir.filter(filter_func=lambda h: h.platform in ("arista_eos", "cisco_xe"))


@pytest.fixture(scope="module")
def bgp_summaries(routing_devices) -> dict[str, dict]:
    """Parallel-collect normalized BGP summary from every routing device.

    Returns: {device_name: normalized_summary_dict}
    """
    from nornir_netmiko.tasks import netmiko_send_command

    raw_results = routing_devices.run(
        task=netmiko_send_command, command_string="show ip bgp summary"
    )

    summaries = {}
    for name, mr in raw_results.items():
        if mr.failed:
            pytest.fail(f"{name}: failed to fetch BGP summary — {mr.exception}")
        os_name = mr.host.data.get("os", "")
        summaries[name] = parse_bgp_summary(os_name=os_name, raw=str(mr.result))
    return summaries


# ---------------------------------------------------------------------------
# Connectivity sanity
# ---------------------------------------------------------------------------
class TestConnectivity:
    """Every managed routing device should be reachable and parseable."""

    def test_all_managed_routing_devices_reachable(self, bgp_summaries):
        assert len(bgp_summaries) >= 11, (
            f"expected at least 11 routing devices, got {len(bgp_summaries)}"
        )

    def test_every_device_returned_a_summary(self, bgp_summaries):
        for name, summary in bgp_summaries.items():
            assert summary, f"{name}: empty summary"
            assert "neighbors" in summary, f"{name}: no 'neighbors' key"


# ---------------------------------------------------------------------------
# BGP session state — the core baseline
# ---------------------------------------------------------------------------
class TestBgpSessionsEstablished:
    """All managed BGP sessions must be Established. This is the foundation
    every future TDD test depends on — if this fails, nothing else makes sense."""

    def test_no_sessions_in_idle_or_active(self, bgp_summaries):
        """No session should be stuck in Idle / Active — that's a real failure."""
        problems = []
        for name, summary in bgp_summaries.items():
            for nbr in summary["neighbors"]:
                if nbr["state"] not in ("Established",):
                    problems.append(f"{name} -> {nbr['neighbor']}: {nbr['state']}")
        assert not problems, "Sessions not Established:\n  " + "\n  ".join(problems)

    def test_dc_underlay_session_count(self, bgp_summaries):
        """DC-East: 8 underlay eBGP sessions (4 leaves/borders × 2 spines)."""
        dc_underlay_devs = [
            "dc-spine-1",
            "dc-spine-2",
            "dc-leaf-1",
            "dc-leaf-2",
            "dc-border-1",
            "dc-border-2",
        ]
        total = sum(len(bgp_summaries[d]["neighbors"]) for d in dc_underlay_devs)
        # Each session counted twice (once on each end) -> 8 sessions = 16 endpoint counts
        assert total == 16, f"expected 16 endpoint counts (8 sessions ×2), got {total}"

    def test_dr_collapsed_underlay(self, bgp_summaries):
        """DR-West: dr-leaf-1 ↔ dr-leaf-2 — exactly 1 session each side."""
        for d in ("dr-leaf-1", "dr-leaf-2"):
            assert len(bgp_summaries[d]["neighbors"]) == 1, (
                f"{d}: expected 1 neighbor, got {len(bgp_summaries[d]['neighbors'])}"
            )

    def test_ce_dual_homed(self, bgp_summaries):
        """Each CE peers with both PEs — exactly 2 sessions."""
        for ce in ("dc-ce-1", "br-ce-1", "dr-ce-1"):
            nbrs = bgp_summaries[ce]["neighbors"]
            assert len(nbrs) == 2, f"{ce}: expected 2 PE neighbors, got {len(nbrs)}"
            for n in nbrs:
                assert n["remote_as"] == 64500, (
                    f"{ce} -> {n['neighbor']}: remote_as {n['remote_as']} != 64500"
                )


# ---------------------------------------------------------------------------
# AS topology — verify the renumbered CE ASNs
# ---------------------------------------------------------------------------
class TestAsTopology:
    """Per-site CE AS renumbering must be in effect (not the legacy shared 65100)."""

    @pytest.mark.parametrize(
        "device,expected_as",
        [
            ("dc-ce-1", 65100),
            ("br-ce-1", 65120),
            ("dr-ce-1", 65130),
            ("dc-spine-1", 65000),
            ("dc-spine-2", 65000),
            ("dc-leaf-1", 65001),
            ("dc-leaf-2", 65002),
            ("dc-border-1", 65003),
            ("dc-border-2", 65004),
            ("dr-leaf-1", 65201),
            ("dr-leaf-2", 65202),
        ],
    )
    def test_local_as(self, bgp_summaries, device, expected_as):
        actual = bgp_summaries[device]["local_as"]
        assert actual == expected_as, f"{device}: local_as {actual} != expected {expected_as}"
