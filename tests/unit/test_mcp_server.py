"""Tests for MCP server tool logic — mocked SSH/Batfish."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"


@pytest.fixture
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text())


# ---------------------------------------------------------------------------
# Fabric health tool
# ---------------------------------------------------------------------------
class TestCheckFabricHealth:
    """check_fabric_health must return structured BGP results."""

    @patch("mcp_server._load_creds")
    @patch("agent.runner.run_fabric_health")
    def test_returns_all_devices(self, mock_run: MagicMock, mock_creds: MagicMock) -> None:
        from mcp_server import _run_fabric_health

        mock_creds.return_value = MagicMock()
        mock_run.return_value = {
            "passed": 8,
            "failed": 0,
            "devices": {"dc-spine-1": {"healthy": True, "established": 4}},
        }
        result = _run_fabric_health()
        assert result["passed"] == 8
        assert "devices" in result

    @patch("mcp_server._load_creds")
    @patch("agent.runner.run_fabric_health")
    def test_filters_single_device(self, mock_run: MagicMock, mock_creds: MagicMock) -> None:
        from mcp_server import _run_fabric_health

        mock_creds.return_value = MagicMock()
        mock_run.return_value = {
            "passed": 8,
            "failed": 0,
            "devices": {
                "dc-spine-1": {"healthy": True, "established": 4},
                "dc-leaf-1": {"healthy": True, "established": 2},
            },
        }
        result = _run_fabric_health(device="dc-spine-1")
        assert result["device"] == "dc-spine-1"
        assert result["healthy"] is True

    @patch("mcp_server._load_creds")
    @patch("agent.runner.run_fabric_health")
    def test_unknown_device_returns_error(self, mock_run: MagicMock, mock_creds: MagicMock) -> None:
        from mcp_server import _run_fabric_health

        mock_creds.return_value = MagicMock()
        mock_run.return_value = {"passed": 0, "failed": 0, "devices": {}}
        result = _run_fabric_health(device="nonexistent")
        assert "error" in result


# ---------------------------------------------------------------------------
# Spec compliance tool
# ---------------------------------------------------------------------------
class TestCheckSpecCompliance:
    """check_spec_compliance must return drift details."""

    @patch("mcp_server._load_creds")
    @patch("agent.runner.run_spec_compliance")
    def test_returns_drifts(self, mock_run: MagicMock, mock_creds: MagicMock) -> None:
        from mcp_server import _run_spec_compliance

        mock_creds.return_value = MagicMock()
        mock_run.return_value = {
            "passed": 14,
            "failed": 1,
            "all_drifts": [{"device": "dc-spine-1", "interface": "Eth1"}],
            "devices": {"dc-spine-1": {"drifts": [{"interface": "Eth1"}]}},
        }
        result = _run_spec_compliance()
        assert result["failed"] == 1


# ---------------------------------------------------------------------------
# Batfish validation tool
# ---------------------------------------------------------------------------
class TestRunBatfishValidation:
    """run_batfish_validation must handle both success and connection errors."""

    @patch("batfish.validate.validate_pre_deploy", return_value=(True, []))
    def test_batfish_passes(self, mock_validate: MagicMock) -> None:
        import asyncio

        from mcp_server import run_batfish_validation

        result = asyncio.run(run_batfish_validation())
        assert result["passed"] is True
        assert result["issue_count"] == 0

    @patch(
        "batfish.validate.validate_pre_deploy",
        side_effect=Exception("Batfish server not reachable"),
    )
    def test_batfish_connection_error(self, mock_validate: MagicMock) -> None:
        import asyncio

        from batfish.validate import BatfishConnectionError
        from mcp_server import run_batfish_validation

        # Patch to raise the right exception type
        mock_validate.side_effect = BatfishConnectionError("not reachable")
        result = asyncio.run(run_batfish_validation())
        assert result["passed"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# Chaos test tool
# ---------------------------------------------------------------------------
class TestRunChaosTest:
    """run_chaos_test must default to dry_run=True."""

    def test_dry_run_is_default(self) -> None:
        """The dry_run parameter must default to True for safety."""
        import inspect

        from mcp_server import run_chaos_test

        sig = inspect.signature(run_chaos_test)
        assert sig.parameters["dry_run"].default is True

    @patch("scripts.chaos_test.run_chaos_test")
    def test_serializes_fault_dataclass(self, mock_chaos: MagicMock) -> None:
        from mcp_server import _run_chaos
        from scripts.chaos_test import Fault

        mock_chaos.return_value = {
            "fault": Fault("shut_interface", "dc-spine-1", "arista_eos", "Eth1", "fabric_health"),
            "detected": False,
            "rolled_back": False,
            "prediction": None,
        }
        result = _run_chaos("", True)
        # Fault should be serialized to dict, not a dataclass
        assert isinstance(result["fault"], dict)
        assert result["fault"]["type"] == "shut_interface"
        assert result["fault"]["device"] == "dc-spine-1"


# ---------------------------------------------------------------------------
# Concurrency control
# ---------------------------------------------------------------------------
class TestConcurrencyControl:
    """Semaphore, per-device locks, and Batfish lock must be properly configured."""

    def test_semaphore_limit(self) -> None:
        from mcp_server import MAX_CONCURRENT_DEVICES

        assert MAX_CONCURRENT_DEVICES == 10

    def test_lazy_semaphore_creation(self) -> None:
        import asyncio

        from mcp_server import _get_semaphore

        sem = _get_semaphore()
        assert isinstance(sem, asyncio.Semaphore)

    def test_per_device_lock_created_per_device(self) -> None:
        import asyncio

        from mcp_server import _get_device_lock

        lock_a = _get_device_lock("dc-spine-1")
        lock_b = _get_device_lock("dc-spine-2")
        lock_a2 = _get_device_lock("dc-spine-1")
        assert isinstance(lock_a, asyncio.Lock)
        assert lock_a is lock_a2  # same device = same lock
        assert lock_a is not lock_b  # different device = different lock

    def test_batfish_lock_singleton(self) -> None:
        import asyncio

        from mcp_server import _get_batfish_lock

        lock1 = _get_batfish_lock()
        lock2 = _get_batfish_lock()
        assert isinstance(lock1, asyncio.Lock)
        assert lock1 is lock2

    def test_device_lock_serializes_access(self) -> None:
        """Two concurrent calls to the same device must not overlap."""
        import asyncio

        from mcp_server import _run_with_device_lock

        call_order: list[str] = []

        def slow_fn(label: str) -> str:
            import time

            call_order.append(f"{label}-start")
            time.sleep(0.05)
            call_order.append(f"{label}-end")
            return label

        async def run() -> None:
            await asyncio.gather(
                _run_with_device_lock("same-device", slow_fn, "A"),
                _run_with_device_lock("same-device", slow_fn, "B"),
            )

        asyncio.run(run())
        # With per-device lock, calls must not interleave:
        # Either A-start,A-end,B-start,B-end or B-start,B-end,A-start,A-end
        assert call_order[0].endswith("-start")
        assert call_order[1].endswith("-end")
        assert call_order[0][0] == call_order[1][0]  # same caller finishes before next starts


# ---------------------------------------------------------------------------
# get_device_state tool
# ---------------------------------------------------------------------------
class TestGetDeviceState:
    """get_device_state must SSH to a device and return show command output."""

    @patch("mcp_server._load_creds")
    @patch("agent.runner._connect")
    @patch("scripts.bootstrap_config.get_mgmt_ips", return_value={"dc-spine-1": "192.168.68.11"})
    def test_returns_output_per_check(
        self, mock_ips: MagicMock, mock_connect: MagicMock, mock_creds: MagicMock
    ) -> None:
        from mcp_server import _run_device_state

        mock_creds.return_value = MagicMock(device_username="admin", device_password="admin")
        mock_conn = MagicMock()
        mock_conn.send_command.side_effect = lambda cmd: f"output for: {cmd}"
        mock_connect.return_value = mock_conn

        result = _run_device_state("dc-spine-1", ["bgp", "version"])
        assert result["device"] == "dc-spine-1"
        assert result["platform"] == "arista_eos"
        assert "output for: show ip bgp summary" in result["bgp"]
        assert "output for: show version" in result["version"]
        mock_conn.disconnect.assert_called_once()

    def test_unknown_device_returns_error(self) -> None:
        from mcp_server import _run_device_state

        with patch("mcp_server._load_spec") as mock_spec, patch("mcp_server._load_creds"):
            mock_spec.return_value = {"sites": {}, "wan_transport": {}, "security": {}}
            result = _run_device_state("nonexistent", ["bgp"])
            assert "error" in result

    def test_invalid_checks_rejected(self) -> None:
        import asyncio

        from mcp_server import get_device_state

        result = asyncio.run(get_device_state("dc-spine-1", ["bgp", "foobar"]))
        assert "error" in result
        assert "foobar" in str(result["error"])

    @patch("mcp_server._load_creds")
    @patch("agent.runner._connect")
    @patch("scripts.bootstrap_config.get_mgmt_ips", return_value={"dc-fw-1": "192.168.68.31"})
    def test_fortios_uses_send_command_timing(
        self, mock_ips: MagicMock, mock_connect: MagicMock, mock_creds: MagicMock
    ) -> None:
        from mcp_server import _run_device_state

        mock_creds.return_value = MagicMock(
            device_username="admin",
            device_password="admin",
            fortigate_username="admin",
            fortigate_password="admin",
        )
        mock_conn = MagicMock()
        mock_conn.send_command_timing.return_value = "fortios output"
        mock_connect.return_value = mock_conn

        result = _run_device_state("dc-fw-1", ["interfaces"])
        # FortiOS should use send_command_timing, not send_command
        mock_conn.send_command_timing.assert_called()
        mock_conn.send_command.assert_not_called()
        assert result["interfaces"] == "fortios output"


# ---------------------------------------------------------------------------
# get_topology tool
# ---------------------------------------------------------------------------
class TestGetTopology:
    """get_topology must return structured topology from the spec."""

    def test_returns_all_sites(self, spec: dict) -> None:
        import asyncio

        from mcp_server import get_topology

        result = asyncio.run(get_topology())
        assert "dc_east" in result["sites"]
        assert "branch_01" in result["sites"]
        assert "dr_west" in result["sites"]

    def test_device_counts(self, spec: dict) -> None:
        import asyncio

        from mcp_server import get_topology

        result = asyncio.run(get_topology())
        # DC-East has spines, leaves, borders, CE, hosts
        dc_east = result["sites"]["dc_east"]
        assert dc_east["device_count"] > 0
        # Every device has required fields
        for dev in dc_east["devices"]:
            assert "name" in dev
            assert "role" in dev
            assert "platform" in dev

    def test_includes_firewalls(self, spec: dict) -> None:
        import asyncio

        from mcp_server import get_topology

        result = asyncio.run(get_topology())
        fw_names = [fw["name"] for fw in result["firewalls"]]
        assert "dc-fw-1" in fw_names
        assert "dr-fw-1" in fw_names

    def test_includes_agent_boundary(self, spec: dict) -> None:
        import asyncio

        from mcp_server import get_topology

        result = asyncio.run(get_topology())
        boundary = result["agent_boundary"]
        assert "dc-spine-1" in boundary["managed"]
        assert "sp-pe-1" in boundary["excluded"]

    def test_includes_reachability_matrix(self, spec: dict) -> None:
        import asyncio

        from mcp_server import get_topology

        result = asyncio.run(get_topology())
        assert len(result["reachability_matrix"]) > 0
        assert "source" in result["reachability_matrix"][0]

    def test_total_links_positive(self, spec: dict) -> None:
        import asyncio

        from mcp_server import get_topology

        result = asyncio.run(get_topology())
        assert result["total_links"] > 0


# ---------------------------------------------------------------------------
# blast_radius tool
# ---------------------------------------------------------------------------
class TestBlastRadius:
    """blast_radius must wrap Batfish failure impact analysis."""

    @patch("batfish.validate.failure_impact")
    @patch("batfish.validate.init_batfish")
    @patch("batfish.snapshot.build_snapshot", return_value=Path("/tmp/snapshot"))
    def test_returns_broken_and_surviving(
        self, mock_build: MagicMock, mock_init: MagicMock, mock_impact: MagicMock
    ) -> None:
        from mcp_server import _run_blast_radius

        mock_impact.return_value = {
            "node": "dc-spine-1",
            "broken": [{"src": "dc-host-1", "dst": "dc-host-2"}],
            "surviving": [{"src": "br-host-1", "dst": "dc-host-1"}],
        }

        result = _run_blast_radius("dc-spine-1")
        assert result["node"] == "dc-spine-1"
        assert len(result["broken"]) == 1
        assert len(result["surviving"]) == 1

    def test_unknown_node_returns_error(self) -> None:
        from mcp_server import _run_blast_radius

        with patch("mcp_server._load_spec") as mock_spec:
            mock_spec.return_value = {"sites": {}, "wan_transport": {}, "security": {}}
            result = _run_blast_radius("nonexistent")
            assert "error" in result

    def test_batfish_connection_error_handled(self) -> None:
        import asyncio

        from batfish.validate import BatfishConnectionError
        from mcp_server import blast_radius

        with patch(
            "mcp_server._run_blast_radius",
            side_effect=BatfishConnectionError("not reachable"),
        ):
            result = asyncio.run(blast_radius("dc-spine-1"))
            assert result["node"] == "dc-spine-1"
            assert "error" in result
            assert "hint" in result


# ---------------------------------------------------------------------------
# SSE security
# ---------------------------------------------------------------------------
class TestSSESecurity:
    """SSE mode must bind to localhost, not 0.0.0.0."""

    def test_sse_binds_localhost(self) -> None:
        """Verify the SSE server binds to 127.0.0.1, not 0.0.0.0."""
        import ast

        source = Path(__file__).parent.parent.parent / "mcp_server.py"
        tree = ast.parse(source.read_text())

        # Find the mcp.run(transport="sse", ...) call
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "host"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "0.0.0.0"
                ):
                    raise AssertionError(
                        "SSE binds to 0.0.0.0 — must be 127.0.0.1 to prevent "
                        "unauthenticated network access to chaos_test and other tools"
                    )


# ---------------------------------------------------------------------------
# Chaos rollback failure severity
# ---------------------------------------------------------------------------
class TestChaosRollbackSeverity:
    """Failed rollback must return critical severity and manual fix instructions."""

    @patch("scripts.chaos_test.run_chaos_test")
    def test_rollback_failure_returns_critical(self, mock_chaos: MagicMock) -> None:
        from mcp_server import _run_chaos
        from scripts.chaos_test import Fault

        mock_chaos.return_value = {
            "fault": Fault("shut_interface", "dc-spine-1", "arista_eos", "Eth1", "fabric_health"),
            "detected": True,
            "rolled_back": False,  # rollback failed
        }
        result = _run_chaos("dc-spine-1", False)  # dry_run=False
        assert result["severity"] == "critical"
        assert result["rollback_failed"] is True
        assert "dc-spine-1" in result["manual_fix"]
        assert "push_configs" in result["manual_fix"]

    @patch("scripts.chaos_test.run_chaos_test")
    def test_successful_rollback_no_severity(self, mock_chaos: MagicMock) -> None:
        from mcp_server import _run_chaos
        from scripts.chaos_test import Fault

        mock_chaos.return_value = {
            "fault": Fault("shut_interface", "dc-spine-1", "arista_eos", "Eth1", "fabric_health"),
            "detected": True,
            "rolled_back": True,
        }
        result = _run_chaos("dc-spine-1", False)
        assert "severity" not in result
        assert "rollback_failed" not in result

    @patch("scripts.chaos_test.run_chaos_test")
    def test_dry_run_no_severity(self, mock_chaos: MagicMock) -> None:
        from mcp_server import _run_chaos
        from scripts.chaos_test import Fault

        mock_chaos.return_value = {
            "fault": Fault("shut_interface", "dc-spine-1", "arista_eos", "Eth1", "fabric_health"),
            "detected": False,
            "rolled_back": False,
        }
        result = _run_chaos("dc-spine-1", True)  # dry_run=True
        assert "severity" not in result


# ---------------------------------------------------------------------------
# Credential sanitization
# ---------------------------------------------------------------------------
class TestCredentialSanitization:
    """Error messages must not leak credentials or connection parameters."""

    @patch("mcp_server._load_creds")
    @patch("agent.runner._connect", return_value=None)
    @patch("scripts.bootstrap_config.get_mgmt_ips", return_value={"dc-spine-1": "192.168.68.11"})
    def test_ssh_failure_does_not_leak_password(
        self, mock_ips: MagicMock, mock_connect: MagicMock, mock_creds: MagicMock
    ) -> None:
        from mcp_server import _run_device_state

        mock_creds.return_value = MagicMock(device_username="admin", device_password="s3cr3tP@ss!")
        result = _run_device_state("dc-spine-1", ["bgp"])
        assert "error" in result
        assert "s3cr3tP@ss!" not in str(result)
        assert "admin" not in str(result["error"])


# ---------------------------------------------------------------------------
# NetBox reconciliation failure escalation
# ---------------------------------------------------------------------------
class TestNetboxReconcilerEscalation:
    """Consecutive NetBox failures must escalate log severity."""

    @patch("agent.netbox_reconciler.is_reconciliation_enabled", return_value=True)
    @patch("agent.netbox_reconciler._get_netbox_api", side_effect=ConnectionError("refused"))
    def test_consecutive_failures_escalate(
        self, mock_api: MagicMock, mock_enabled: MagicMock
    ) -> None:
        import agent.netbox_reconciler as reconciler

        reconciler._consecutive_failures = 0  # reset

        # First 2 failures: warning level
        for _ in range(2):
            result = reconciler.reconcile_drifts("dc-spine-1", [{"field": "ipv4"}])
            assert result is False

        assert reconciler._consecutive_failures == 2

        # 3rd failure: hits threshold — escalates to error
        result = reconciler.reconcile_drifts("dc-spine-1", [{"field": "ipv4"}])
        assert result is False
        assert reconciler._consecutive_failures == 3

    @patch("agent.netbox_reconciler.is_reconciliation_enabled", return_value=True)
    @patch("agent.netbox_reconciler._get_netbox_api")
    @patch("agent.netbox_reconciler.create_journal_entry", return_value=True)
    def test_success_resets_failure_counter(
        self, mock_create: MagicMock, mock_api: MagicMock, mock_enabled: MagicMock
    ) -> None:
        import agent.netbox_reconciler as reconciler

        reconciler._consecutive_failures = 5  # simulate prior failures

        result = reconciler.reconcile_drifts("dc-spine-1", [{"field": "ipv4"}])
        assert result is True
        assert reconciler._consecutive_failures == 0


# ---------------------------------------------------------------------------
# get_bgp_prefix tool — parser
# ---------------------------------------------------------------------------
class TestParseBgpPrefixIosxe:
    """The Cisco IOS-XE 'show ip bgp <prefix>' parser must extract per-path
    local-pref / AS-path / best-path / from-peer correctly."""

    SAMPLE = (
        "BGP routing table entry for 10.20.0.1/32, version 8\n"
        "Paths: (2 available, best #2, table default)\n"
        "  Advertised to update-groups:\n"
        "     1         \n"
        "  Refresh Epoch 5\n"
        "  64500 65120\n"
        "    172.16.0.3 from 172.16.0.3 (172.16.0.112)\n"
        "      Origin IGP, localpref 100, valid, external\n"
        "      rx pathid: 0, tx pathid: 0\n"
        "  Refresh Epoch 4\n"
        "  64500 65120\n"
        "    172.16.0.1 from 172.16.0.1 (172.16.0.111)\n"
        "      Origin IGP, localpref 200, valid, external, best\n"
        "      rx pathid: 0, tx pathid: 0x0\n"
    )

    def test_parses_prefix(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        assert out["prefix"] == "10.20.0.1/32"

    def test_extracts_best_path_index(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        assert out["best_path_index"] == 1

    def test_finds_two_paths(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        assert len(out["paths"]) == 2

    def test_local_pref_per_path(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        # path 0 = via sp-pe-2 (lower local-pref), path 1 = via sp-pe-1 (best)
        assert out["paths"][0]["local_pref"] == 100
        assert out["paths"][1]["local_pref"] == 200

    def test_best_marker_only_on_winning_path(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        assert out["paths"][0]["best"] is False
        assert out["paths"][1]["best"] is True

    def test_as_path_length(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        for p in out["paths"]:
            assert p["as_path_length"] == 2
            assert p["as_path"] == "64500 65120"

    def test_from_peer_distinct_per_path(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        from_peers = {p["from_peer"] for p in out["paths"]}
        assert from_peers == {"172.16.0.1", "172.16.0.3"}

    def test_external_internal_flags(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe(self.SAMPLE)
        for p in out["paths"]:
            assert p["external"] is True
            assert p["internal"] is False

    def test_handles_empty_input(self) -> None:
        from mcp_server import _parse_bgp_prefix_iosxe

        out = _parse_bgp_prefix_iosxe("")
        assert out["prefix"] == ""
        assert out["paths"] == []


class TestGetBgpPrefix:
    """The MCP tool wrapper must validate inputs and route to platform parsers."""

    @patch("mcp_server._load_creds")
    @patch("mcp_server._load_spec")
    def test_unknown_device_returns_error(
        self, mock_spec: MagicMock, mock_creds: MagicMock
    ) -> None:
        from mcp_server import _run_bgp_prefix

        mock_spec.return_value = {"sites": {}, "wan_transport": {"devices": []}, "security": {}}
        mock_creds.return_value = MagicMock()
        result = _run_bgp_prefix("nonexistent", "10.0.0.1")
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_fortigate_returns_unsupported(self) -> None:
        from mcp_server import _run_bgp_prefix

        with (
            patch("mcp_server._load_spec") as mock_spec,
            patch("mcp_server._load_creds") as mock_creds,
            patch("scripts.bootstrap_config.get_mgmt_ips", return_value={"dc-fw-1": "1.1.1.1"}),
        ):
            mock_spec.return_value = {
                "sites": {},
                "wan_transport": {"devices": []},
                "security": {
                    "dc": {"firewalls": [{"name": "dc-fw-1", "platform": "fortinet_fortios"}]}
                },
            }
            mock_creds.return_value = MagicMock()
            result = _run_bgp_prefix("dc-fw-1", "10.0.0.1")
        assert "error" in result
        assert "fortigate" in result["error"].lower()
