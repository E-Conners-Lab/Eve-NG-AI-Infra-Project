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
    """Semaphore and per-device locks must be properly configured."""

    def test_semaphore_limit(self) -> None:
        from mcp_server import MAX_CONCURRENT_DEVICES

        assert MAX_CONCURRENT_DEVICES == 4

    def test_lazy_semaphore_creation(self) -> None:
        import asyncio

        from mcp_server import _get_semaphore

        sem = _get_semaphore()
        assert isinstance(sem, asyncio.Semaphore)
