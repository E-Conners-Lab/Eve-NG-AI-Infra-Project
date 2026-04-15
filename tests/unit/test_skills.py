"""Tests for agent skills — TDD.

Written BEFORE the skill implementations. Each test validates that a skill
correctly parses device output, identifies drift, and respects boundaries.
Uses mocked device responses — no live devices needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"


@pytest.fixture
def spec() -> dict:
    """Load the full lab spec."""
    return yaml.safe_load(SPEC_PATH.read_text())


# ---------------------------------------------------------------------------
# Sample device outputs for mocking
# ---------------------------------------------------------------------------

EOS_BGP_SUMMARY = """
BGP summary information for VRF default
Router identifier 10.1.0.1, local AS number 65000
Neighbor Status Codes: m - Under maintenance
  Neighbor    V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc
  10.1.1.1    4 65001            120       118    0    0 01:30:00 Estab   5      5
  10.1.1.3    4 65002            118       120    0    0 01:30:00 Estab   5      5
  10.1.1.5    4 65003            115       117    0    0 01:30:00 Estab   3      3
  10.1.1.7    4 65004            112       114    0    0 01:30:00 Estab   3      3
"""

EOS_BGP_SUMMARY_WITH_FAILURE = """
BGP summary information for VRF default
Router identifier 10.1.0.1, local AS number 65000
  Neighbor    V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc
  10.1.1.1    4 65001            120       118    0    0 01:30:00 Estab   5      5
  10.1.1.3    4 65002              0         0    0    0 00:00:05 Active  0      0
  10.1.1.5    4 65003            115       117    0    0 01:30:00 Estab   3      3
  10.1.1.7    4 65004            112       114    0    0 01:30:00 Estab   3      3
"""

IOSXE_BGP_SUMMARY = """
BGP router identifier 10.20.0.1, local AS number 65100
BGP table version is 5, main routing table version 5

Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
172.16.0.6      4        64500      85      82        5    0    0 01:10:00        3
172.16.0.8      4        64500      83      80        5    0    0 01:10:00        3
"""

IOSXE_BGP_ONE_DOWN = """
BGP router identifier 10.20.0.1, local AS number 65100

Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
172.16.0.6      4        64500      85      82        5    0    0 01:10:00        3
172.16.0.8      4        64500       0       0        0    0    0 00:00:10 Idle
"""

EOS_SHOW_IP_INTERFACE = """
Interface   IP Address     Status  Protocol  MTU   Owner
Ethernet1   10.1.1.0/31    up      up        1500
Ethernet2   10.1.1.2/31    up      up        1500
Ethernet3   10.1.1.4/31    up      up        1500
Ethernet4   10.1.1.6/31    up      up        1500
Loopback0   10.1.0.1/32    up      up        65535
Management1 192.168.68.110/22  up  up        1500
"""

EOS_SHOW_IP_INTERFACE_DRIFTED = """
Interface   IP Address     Status  Protocol  MTU   Owner
Ethernet1   10.1.1.0/31    up      up        1500
Ethernet2   10.99.99.1/31  up      up        1500
Ethernet3   10.1.1.4/31    up      up        1500
Ethernet4   10.1.1.6/31    up      up        1500
Loopback0   10.1.0.1/32    up      up        65535
"""


# ---------------------------------------------------------------------------
# Test 1: fabric_health — BGP session parsing
# ---------------------------------------------------------------------------
class TestFabricHealth:
    """fabric_health skill must parse BGP output and identify session states."""

    def test_parse_eos_bgp_all_established(self) -> None:
        """All BGP sessions Established = healthy."""
        from agent.skills.fabric_health.skill import parse_bgp_summary

        result = parse_bgp_summary(EOS_BGP_SUMMARY, "arista_eos")
        assert result["total_neighbors"] == 4
        assert result["established"] == 4
        assert result["down"] == 0
        assert result["healthy"] is True

    def test_parse_eos_bgp_with_down_session(self) -> None:
        """One BGP session Active = unhealthy."""
        from agent.skills.fabric_health.skill import parse_bgp_summary

        result = parse_bgp_summary(EOS_BGP_SUMMARY_WITH_FAILURE, "arista_eos")
        assert result["total_neighbors"] == 4
        assert result["established"] == 3
        assert result["down"] == 1
        assert result["healthy"] is False
        assert any("10.1.1.3" in n["neighbor"] for n in result["down_neighbors"])

    def test_parse_returns_neighbor_details(self) -> None:
        """Parser must return per-neighbor ASN, state, and prefix count."""
        from agent.skills.fabric_health.skill import parse_bgp_summary

        result = parse_bgp_summary(EOS_BGP_SUMMARY, "arista_eos")
        neighbors = result["neighbors"]
        assert len(neighbors) == 4
        first = neighbors[0]
        assert "neighbor" in first
        assert "asn" in first
        assert "state" in first
        assert "prefixes_received" in first


# ---------------------------------------------------------------------------
# Test 2: spec_compliance — drift detection
# ---------------------------------------------------------------------------
class TestSpecCompliance:
    """spec_compliance skill must identify drift between spec and live state."""

    def test_no_drift_when_matching(self, spec: dict) -> None:
        """No drift when live state matches spec."""
        from agent.skills.spec_compliance.skill import compare_interfaces

        # Simulate dc-spine-1 live output matching spec
        dc_spine = None
        for dev in spec["sites"]["dc_east"]["devices"]:
            if dev["name"] == "dc-spine-1":
                dc_spine = dev
                break

        live_interfaces = {}
        for iface in dc_spine.get("interfaces", []):
            if "ipv4" in iface:
                live_interfaces[iface["name"]] = iface["ipv4"]

        drifts = compare_interfaces("dc-spine-1", dc_spine, live_interfaces)
        assert len(drifts) == 0

    def test_detects_ip_drift(self, spec: dict) -> None:
        """Must detect when a live interface IP differs from spec."""
        from agent.skills.spec_compliance.skill import compare_interfaces

        dc_spine = None
        for dev in spec["sites"]["dc_east"]["devices"]:
            if dev["name"] == "dc-spine-1":
                dc_spine = dev
                break

        # Simulate drift: Ethernet2 has wrong IP
        live_interfaces = {}
        for iface in dc_spine.get("interfaces", []):
            if "ipv4" in iface:
                live_interfaces[iface["name"]] = iface["ipv4"]
        live_interfaces["Ethernet2"] = "10.99.99.1/31"  # DRIFTED

        drifts = compare_interfaces("dc-spine-1", dc_spine, live_interfaces)
        assert len(drifts) >= 1
        drift = drifts[0]
        assert drift["device"] == "dc-spine-1"
        assert drift["interface"] == "Ethernet2"
        assert "10.99.99.1" in drift["live"]
        assert drift["expected"] == dc_spine["interfaces"][1]["ipv4"]

    def test_detects_missing_interface(self, spec: dict) -> None:
        """Must detect when a spec interface is missing from live state."""
        from agent.skills.spec_compliance.skill import compare_interfaces

        dc_spine = None
        for dev in spec["sites"]["dc_east"]["devices"]:
            if dev["name"] == "dc-spine-1":
                dc_spine = dev
                break

        # Simulate missing: Ethernet3 not in live
        live_interfaces = {}
        for iface in dc_spine.get("interfaces", []):
            if "ipv4" in iface and iface["name"] != "Ethernet3":
                live_interfaces[iface["name"]] = iface["ipv4"]

        drifts = compare_interfaces("dc-spine-1", dc_spine, live_interfaces)
        missing = [d for d in drifts if d["interface"] == "Ethernet3"]
        assert len(missing) == 1
        assert "missing" in missing[0]["live"].lower()

    def test_detects_asn_drift(self, spec: dict) -> None:
        """Must detect when live ASN differs from spec."""
        from agent.skills.spec_compliance.skill import compare_asn

        result = compare_asn("dc-spine-1", expected_asn=65000, live_asn=65000)
        assert result is None  # No drift

        result = compare_asn("dc-spine-1", expected_asn=65000, live_asn=65999)
        assert result is not None
        assert result["expected"] == 65000
        assert result["live"] == 65999


# ---------------------------------------------------------------------------
# Test 3: branch_connectivity — dual-homed BGP
# ---------------------------------------------------------------------------
class TestBranchConnectivity:
    """branch_connectivity skill must validate dual-homed BGP to both PEs."""

    def test_both_pe_sessions_up(self) -> None:
        """Both PE sessions Established = healthy."""
        from agent.skills.branch_connectivity.skill import parse_bgp_summary

        result = parse_bgp_summary(IOSXE_BGP_SUMMARY, "cisco_iosxe")
        assert result["total_neighbors"] == 2
        assert result["established"] == 2
        assert result["healthy"] is True

    def test_one_pe_session_down(self) -> None:
        """One PE down = unhealthy (single-homed is a risk)."""
        from agent.skills.branch_connectivity.skill import parse_bgp_summary

        result = parse_bgp_summary(IOSXE_BGP_ONE_DOWN, "cisco_iosxe")
        assert result["total_neighbors"] == 2
        assert result["established"] == 1
        assert result["down"] == 1
        assert result["healthy"] is False

    def test_identifies_down_neighbor(self) -> None:
        """Must identify which PE neighbor is down."""
        from agent.skills.branch_connectivity.skill import parse_bgp_summary

        result = parse_bgp_summary(IOSXE_BGP_ONE_DOWN, "cisco_iosxe")
        down = result["down_neighbors"]
        assert len(down) == 1
        assert down[0]["neighbor"] == "172.16.0.8"
        assert down[0]["state"] == "Idle"


# ---------------------------------------------------------------------------
# Test 4: Agent boundary enforcement
# ---------------------------------------------------------------------------
class TestAgentBoundary:
    """Agent must NEVER attempt to connect to excluded devices."""

    def test_managed_devices_only(self, spec: dict) -> None:
        """Nornir inventory must contain only managed devices."""
        from agent.skills.fabric_health.skill import get_managed_devices

        managed = get_managed_devices(spec)
        expected = set(spec["agent"]["boundary"]["managed"])
        assert set(managed) == expected

    def test_excluded_devices_not_in_inventory(self, spec: dict) -> None:
        """Excluded devices must NOT appear in the managed list."""
        from agent.skills.fabric_health.skill import get_managed_devices

        managed = get_managed_devices(spec)
        excluded = set(spec["agent"]["boundary"]["excluded"])
        overlap = set(managed) & excluded
        assert len(overlap) == 0, f"Excluded devices in managed list: {overlap}"

    def test_firewalls_never_contacted(self, spec: dict) -> None:
        """Firewalls are explicitly excluded — must never be in managed."""
        from agent.skills.fabric_health.skill import get_managed_devices

        managed = get_managed_devices(spec)
        firewalls = {"dc-fw-1", "dc-fw-2", "dr-fw-1", "dr-fw-2"}
        assert firewalls.isdisjoint(set(managed))
