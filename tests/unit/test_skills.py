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

# EOS 4.33+ format — Description column appears before Neighbor IP
EOS_BGP_SUMMARY_433 = """
BGP summary information for VRF default
Router identifier 10.1.0.1, local AS number 65000
Neighbor Status Codes: m - Under maintenance
  Description              Neighbor V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc
  dc-leaf-1                10.1.1.1 4 65001             11        14    0    0 00:05:17 Estab   1      1
  dc-leaf-2                10.1.1.3 4 65002             11        13    0    0 00:05:18 Estab   1      1
  dc-border-1              10.1.1.5 4 65003             11        13    0    0 00:05:18 Estab   3      3
  dc-border-2              10.1.1.7 4 65004             11        13    0    0 00:05:17 Estab   3      3
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

    def test_parse_eos_433_description_first(self) -> None:
        """EOS 4.33+ puts Description before Neighbor IP — parser must handle it."""
        from agent.skills.fabric_health.skill import parse_bgp_summary

        result = parse_bgp_summary(EOS_BGP_SUMMARY_433, "arista_eos")
        assert result["total_neighbors"] == 4
        assert result["established"] == 4
        assert result["down"] == 0
        assert result["healthy"] is True
        # Verify correct IP extraction despite Description column
        ips = {n["neighbor"] for n in result["neighbors"]}
        assert ips == {"10.1.1.1", "10.1.1.3", "10.1.1.5", "10.1.1.7"}


# ---------------------------------------------------------------------------
# Test: Per-platform interface parsers
# ---------------------------------------------------------------------------
class TestInterfaceParsers:
    """Platform-specific parsers for show ip interface brief / equivalents."""

    def test_parse_eos_interfaces(self) -> None:
        from agent.skills.spec_compliance.skill import parse_eos_interfaces

        output = (
            "Interface   IP Address     Status  Protocol  MTU   Owner\n"
            "Ethernet1   10.1.1.0/31    up      up        1500\n"
            "Loopback0   10.1.0.1/32    up      up        65535\n"
            "Management1 192.168.68.110/22  up  up        1500\n"
        )
        result = parse_eos_interfaces(output)
        assert result["Ethernet1"] == "10.1.1.0/31"
        assert result["Loopback0"] == "10.1.0.1/32"
        assert result["Management1"] == "192.168.68.110/22"

    def test_parse_iosxe_interfaces(self) -> None:
        from agent.skills.spec_compliance.skill import parse_iosxe_interfaces

        output = (
            "Interface              IP-Address      OK? Method Status                Protocol\n"
            "GigabitEthernet1       172.16.0.0      YES manual up                    up\n"
            "GigabitEthernet5       192.168.68.120  YES manual up                    up\n"
            "GigabitEthernet6       unassigned      YES unset  administratively down down\n"
            "Loopback0              172.16.0.101    YES manual up                    up\n"
        )
        result = parse_iosxe_interfaces(output)
        assert result["GigabitEthernet1"] == "172.16.0.0"
        assert result["GigabitEthernet5"] == "192.168.68.120"
        assert result["Loopback0"] == "172.16.0.101"
        assert "GigabitEthernet6" not in result  # unassigned excluded

    def test_parse_fortios_interfaces(self) -> None:
        from agent.skills.spec_compliance.skill import parse_fortios_interfaces

        output = (
            "== [onboard]\n"
            "\t==[port1]\n"
            "\t\tmode: static\n"
            "\t\tip: 10.99.0.1 255.255.255.254\n"
            "\t\tstatus: up\n"
            "\t==[port2]\n"
            "\t\tmode: static\n"
            "\t\tip: 10.99.1.0 255.255.255.254\n"
            "\t\tstatus: up\n"
            "\t==[port3]\n"
            "\t\tmode: static\n"
            "\t\tip: 0.0.0.0 0.0.0.0\n"
            "\t\tstatus: down\n"
        )
        result = parse_fortios_interfaces(output)
        assert result["port1"] == "10.99.0.1/31"
        assert result["port2"] == "10.99.1.0/31"
        assert "port3" not in result  # 0.0.0.0 excluded

    def test_mask_to_prefix(self) -> None:
        from agent.skills.spec_compliance.skill import _mask_to_prefix

        assert _mask_to_prefix("255.255.255.254") == 31
        assert _mask_to_prefix("255.255.255.0") == 24
        assert _mask_to_prefix("255.255.252.0") == 22
        assert _mask_to_prefix("255.255.255.255") == 32

    def test_compare_bare_ip_against_cidr(self) -> None:
        """Cisco bare IPs must match spec CIDR when host portion is the same."""
        from agent.skills.spec_compliance.skill import compare_interfaces

        device_spec = {
            "interfaces": [
                {"name": "GigabitEthernet1", "ipv4": "172.16.0.0/31"},
                {"name": "GigabitEthernet2", "ipv4": "10.99.1.1/31"},
            ]
        }
        # Cisco returns bare IPs
        live = {"GigabitEthernet1": "172.16.0.0", "GigabitEthernet2": "10.99.1.1"}
        drifts = compare_interfaces("dc-ce-1", device_spec, live)
        assert len(drifts) == 0

    def test_compare_bare_ip_detects_drift(self) -> None:
        """Cisco bare IPs must detect drift when host IP differs."""
        from agent.skills.spec_compliance.skill import compare_interfaces

        device_spec = {"interfaces": [{"name": "GigabitEthernet1", "ipv4": "172.16.0.0/31"}]}
        live = {"GigabitEthernet1": "172.16.0.99"}
        drifts = compare_interfaces("dc-ce-1", device_spec, live)
        assert len(drifts) == 1
        assert drifts[0]["live"] == "172.16.0.99"


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

    def test_sp_routers_never_contacted(self, spec: dict) -> None:
        """SP PE routers are the only excluded devices — never in managed."""
        from agent.skills.fabric_health.skill import get_managed_devices

        managed = get_managed_devices(spec)
        sp_routers = {"sp-pe-1", "sp-pe-2"}
        assert sp_routers.isdisjoint(set(managed)), "SP PEs must not be in managed"

    def test_firewalls_are_managed(self, spec: dict) -> None:
        """Firewalls are customer equipment — must be in managed."""
        from agent.skills.fabric_health.skill import get_managed_devices

        managed = set(get_managed_devices(spec))
        firewalls = {"dc-fw-1", "dc-fw-2", "dr-fw-1", "dr-fw-2"}
        assert firewalls.issubset(managed), "Firewalls must be managed"

    def test_ce_routers_are_managed(self, spec: dict) -> None:
        """CE routers are customer equipment — must be in managed."""
        from agent.skills.fabric_health.skill import get_managed_devices

        managed = set(get_managed_devices(spec))
        ce_routers = {"dc-ce-1", "dr-ce-1", "br-ce-1"}
        assert ce_routers.issubset(managed), "CE routers must be managed"
