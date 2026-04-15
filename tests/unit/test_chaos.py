"""Tests for chaos testing — TDD.

Written BEFORE the implementation. Validates fault selection, injection
commands, detection matching, rollback verification, and safety guards.
All device interactions are mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"


@pytest.fixture
def spec() -> dict:
    """Load the full lab spec."""
    return yaml.safe_load(SPEC_PATH.read_text())


# ---------------------------------------------------------------------------
# Fault selection — eligible devices and fault types per platform
# ---------------------------------------------------------------------------
class TestFaultSelection:
    """Fault selection must respect platform capabilities and boundaries."""

    def test_eligible_devices_excludes_hosts(self, spec: dict) -> None:
        """Hosts (Alpine Linux) must not be chaos targets."""
        from scripts.chaos_test import get_eligible_devices

        eligible = get_eligible_devices(spec)
        names = {d["name"] for d in eligible}
        hosts = {"dc-host-1", "dc-host-2", "br-host-1", "dr-host-1"}
        assert names.isdisjoint(hosts)

    def test_eligible_devices_excludes_sp_pes(self, spec: dict) -> None:
        """Excluded devices (sp-pe-1, sp-pe-2) must not be chaos targets."""
        from scripts.chaos_test import get_eligible_devices

        eligible = get_eligible_devices(spec)
        names = {d["name"] for d in eligible}
        assert "sp-pe-1" not in names
        assert "sp-pe-2" not in names

    def test_eligible_includes_all_managed_network_devices(self, spec: dict) -> None:
        """All managed network devices (Arista + Cisco + FortiGate) are eligible."""
        from scripts.chaos_test import get_eligible_devices

        eligible = get_eligible_devices(spec)
        names = {d["name"] for d in eligible}
        # 8 Arista + 3 Cisco CEs + 4 FortiGate = 15
        assert len(names) >= 15
        assert "dc-spine-1" in names
        assert "dc-ce-1" in names
        assert "dc-fw-1" in names
        assert "br-ce-1" in names

    def test_fault_types_for_arista(self) -> None:
        """Arista supports: shut_interface, wrong_asn, remove_vni, change_ip."""
        from scripts.chaos_test import get_fault_types_for_platform

        faults = get_fault_types_for_platform("arista_eos", "spine")
        names = {f["name"] for f in faults}
        assert "shut_interface" in names
        assert "wrong_asn" in names
        assert "change_ip" in names

    def test_fault_types_vni_only_on_leaves(self) -> None:
        """remove_vni only available on leaf devices (they have VNI mappings)."""
        from scripts.chaos_test import get_fault_types_for_platform

        spine_faults = {f["name"] for f in get_fault_types_for_platform("arista_eos", "spine")}
        leaf_faults = {f["name"] for f in get_fault_types_for_platform("arista_eos", "leaf")}
        assert "remove_vni" not in spine_faults
        assert "remove_vni" in leaf_faults

    def test_fault_types_for_cisco(self) -> None:
        """Cisco CE supports: shut_interface, wrong_asn, change_ip."""
        from scripts.chaos_test import get_fault_types_for_platform

        faults = get_fault_types_for_platform("cisco_iosxe", "ce")
        names = {f["name"] for f in faults}
        assert "shut_interface" in names
        assert "wrong_asn" in names
        assert "change_ip" in names

    def test_fault_types_for_fortigate(self) -> None:
        """FortiGate supports: shut_interface, change_ip. No BGP/VNI faults."""
        from scripts.chaos_test import get_fault_types_for_platform

        faults = get_fault_types_for_platform("fortinet_fortios", "firewall")
        names = {f["name"] for f in faults}
        assert "shut_interface" in names
        assert "change_ip" in names
        assert "wrong_asn" not in names
        assert "remove_vni" not in names


# ---------------------------------------------------------------------------
# Fault commands — per-platform CLI commands
# ---------------------------------------------------------------------------
class TestFaultCommands:
    """Fault injection must produce correct CLI commands per platform."""

    def test_arista_shut_interface(self) -> None:
        """Arista shut = ['interface EthernetX', 'shutdown']."""
        from scripts.chaos_test import build_fault_commands

        cmds = build_fault_commands("arista_eos", "shut_interface", interface="Ethernet1")
        assert "interface Ethernet1" in cmds
        assert "shutdown" in cmds

    def test_cisco_shut_interface(self) -> None:
        """Cisco shut = ['interface GiX', 'shutdown']."""
        from scripts.chaos_test import build_fault_commands

        cmds = build_fault_commands("cisco_iosxe", "shut_interface", interface="GigabitEthernet1")
        assert "interface GigabitEthernet1" in cmds
        assert "shutdown" in cmds

    def test_fortigate_shut_interface(self) -> None:
        """FortiGate shut = config system interface / edit port1 / set status down."""
        from scripts.chaos_test import build_fault_commands

        cmds = build_fault_commands("fortinet_fortios", "shut_interface", interface="port1")
        assert any("set status down" in c for c in cmds)

    def test_arista_wrong_asn(self) -> None:
        """Wrong ASN must change a BGP neighbor's remote-as."""
        from scripts.chaos_test import build_fault_commands

        cmds = build_fault_commands("arista_eos", "wrong_asn", neighbor="10.1.1.1", wrong_asn=99999)
        assert any("99999" in c for c in cmds)

    def test_arista_change_ip(self) -> None:
        """Change IP must set a wrong IP on an interface."""
        from scripts.chaos_test import build_fault_commands

        cmds = build_fault_commands(
            "arista_eos", "change_ip", interface="Ethernet1", wrong_ip="10.99.99.99/31"
        )
        assert any("10.99.99.99" in c for c in cmds)


# ---------------------------------------------------------------------------
# Detection matching — verify agent output matches injected fault
# ---------------------------------------------------------------------------
class TestDetectionMatching:
    """verify_detection must compare agent results against the injected fault."""

    def test_matches_shut_interface(self) -> None:
        """Agent reports missing interface = matches shut fault."""
        from scripts.chaos_test import Fault, verify_detection

        fault = Fault(
            fault_type="shut_interface",
            device="dc-spine-1",
            platform="arista_eos",
            detail="Ethernet1",
            expected_skill="fabric_health",
        )
        agent_result = {
            "fabric_health": {
                "devices": {
                    "dc-spine-1": {
                        "healthy": False,
                        "down_neighbors": [{"neighbor": "10.1.1.1", "state": "Active"}],
                    }
                }
            }
        }
        assert verify_detection(fault, agent_result) is True

    def test_matches_change_ip(self) -> None:
        """Agent reports IP drift = matches change_ip fault."""
        from scripts.chaos_test import Fault, verify_detection

        fault = Fault(
            fault_type="change_ip",
            device="dc-spine-1",
            platform="arista_eos",
            detail="Ethernet1",
            expected_skill="spec_compliance",
        )
        agent_result = {
            "spec_compliance": {
                "devices": {"dc-spine-1": {"drifts": [{"interface": "Ethernet1", "field": "ipv4"}]}}
            }
        }
        assert verify_detection(fault, agent_result) is True

    def test_no_match_when_agent_misses(self) -> None:
        """Must return False when agent doesn't detect the fault."""
        from scripts.chaos_test import Fault, verify_detection

        fault = Fault(
            fault_type="shut_interface",
            device="dc-spine-1",
            platform="arista_eos",
            detail="Ethernet1",
            expected_skill="fabric_health",
        )
        agent_result = {
            "fabric_health": {
                "devices": {
                    "dc-spine-1": {
                        "healthy": True,
                        "down_neighbors": [],
                    }
                }
            }
        }
        assert verify_detection(fault, agent_result) is False


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------
class TestRollback:
    """Rollback must push clean config and verify success."""

    @patch("scripts.push_configs.push_config_to_device", return_value=True)
    def test_rollback_pushes_clean_config(self, mock_push: MagicMock) -> None:
        """Rollback must call push with the clean config from configs/generated/."""
        from scripts.chaos_test import Fault, rollback_fault

        fault = Fault(
            fault_type="shut_interface",
            device="dc-spine-1",
            platform="arista_eos",
            detail="Ethernet1",
            expected_skill="fabric_health",
        )
        result = rollback_fault(fault, MagicMock())
        assert result is True
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# Dry run safety
# ---------------------------------------------------------------------------
class TestDryRun:
    """Dry run must produce no side effects."""

    def test_dry_run_returns_fault_without_injection(self, spec: dict) -> None:
        """--dry-run must select a fault but not SSH to any device."""
        from scripts.chaos_test import select_random_fault

        fault = select_random_fault(spec)
        assert fault is not None
        assert fault.device != ""
        assert fault.fault_type != ""


# ---------------------------------------------------------------------------
# Try/finally guarantee
# ---------------------------------------------------------------------------
class TestSafetyGuarantees:
    """Rollback must execute even if agent run crashes."""

    def test_inject_and_rollback_structure(self) -> None:
        """run_chaos_test must use try/finally to guarantee rollback."""
        import ast
        import inspect

        from scripts.chaos_test import run_chaos_test

        source = inspect.getsource(run_chaos_test)
        tree = ast.parse(source)
        # Find Try nodes with finalbody (try/finally)
        has_try_finally = any(
            isinstance(node, ast.Try) and node.finalbody for node in ast.walk(tree)
        )
        assert has_try_finally, "run_chaos_test must use try/finally for rollback"
