"""Integration tests for EVE-NG device connectivity.

These tests require live EVE-NG devices with management IPs configured.
Skip with: pytest -m "not integration"

Marked with @pytest.mark.integration so they don't run in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"


@pytest.fixture
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text())


# ---------------------------------------------------------------------------
# Unit tests for push logic (mocked SSH — always run)
# ---------------------------------------------------------------------------
class TestPushConfigLogic:
    """Test config push logic without requiring live devices."""

    def test_push_skips_missing_config_file(self, tmp_path: Path) -> None:
        """Push must skip devices with no config file."""
        from scripts.push_configs import push_config_to_device

        log_file = tmp_path / "test.jsonl"
        result = push_config_to_device(
            "test-device",
            "arista_eos",
            tmp_path / "nonexistent.cfg",
            "192.168.68.110",
            "admin",
            "admin",
            log_file,
        )
        assert result is False
        assert log_file.exists()

    def test_push_skips_empty_config(self, tmp_path: Path) -> None:
        """Push must skip empty config files."""
        from scripts.push_configs import push_config_to_device

        cfg = tmp_path / "empty.cfg"
        cfg.write_text("")
        log_file = tmp_path / "test.jsonl"
        result = push_config_to_device(
            "test-device",
            "arista_eos",
            cfg,
            "192.168.68.110",
            "admin",
            "admin",
            log_file,
        )
        assert result is False

    def test_push_skips_unsupported_platform(self, tmp_path: Path) -> None:
        """Push must skip devices with no Scrapli driver."""
        from scripts.push_configs import push_config_to_device

        cfg = tmp_path / "test.cfg"
        cfg.write_text("hostname test")
        log_file = tmp_path / "test.jsonl"
        result = push_config_to_device(
            "test-device",
            "linux",
            cfg,
            "192.168.68.130",
            "admin",
            "admin",
            log_file,
        )
        assert result is False

    def test_push_logs_every_action(self, tmp_path: Path) -> None:
        """Every push attempt must produce a GAIT log entry."""
        from scripts.push_configs import push_config_to_device

        cfg = tmp_path / "test.cfg"
        cfg.write_text("hostname test\ninterface Ethernet1")
        log_file = tmp_path / "test.jsonl"

        # Dry run to generate log entries
        push_config_to_device(
            "dc-spine-1",
            "arista_eos",
            cfg,
            "192.168.68.110",
            "admin",
            "admin",
            log_file,
            dry_run=True,
        )

        import json

        lines = log_file.read_text().splitlines()
        assert len(lines) >= 2  # push_start + push_dry_run
        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "action" in entry
            assert "device" in entry

    def test_push_handles_connection_failure(self, tmp_path: Path) -> None:
        """Push must fail gracefully if device is unreachable."""
        from scripts.push_configs import push_config_to_device

        cfg = tmp_path / "test.cfg"
        cfg.write_text("hostname test")
        log_file = tmp_path / "test.jsonl"

        # Use an unreachable IP — should fail, not crash
        result = push_config_to_device(
            "fake-device",
            "arista_eos",
            cfg,
            "192.0.2.1",
            "admin",
            "admin",  # RFC 5737 TEST-NET — unreachable
            log_file,
        )
        assert result is False
        assert log_file.exists()


class TestReachabilityLogic:
    """Test reachability script logic."""

    def test_empty_matrix_returns_zero(self, spec: dict) -> None:
        """No tests defined should return 0 passed, 0 failed."""
        from scripts.run_reachability import run_reachability

        empty_spec = {**spec, "tests": {"reachability_matrix": []}}
        passed, failed = run_reachability(empty_spec, dry_run=True)
        assert passed == 0
        assert failed == 0

    def test_dry_run_passes_all(self, spec: dict) -> None:
        """Dry run should pass all reachability tests."""
        from scripts.run_reachability import run_reachability

        passed, failed = run_reachability(spec, dry_run=True)
        matrix_len = len(spec.get("tests", {}).get("reachability_matrix", []))
        assert passed + failed == matrix_len
        assert failed == 0


class TestTestbedGeneration:
    """Test testbed.yaml generation."""

    def test_testbed_has_managed_devices(self, spec: dict) -> None:
        """Generated testbed must include all managed devices."""
        from scripts.generate_testbed import generate_testbed

        testbed = generate_testbed(spec)
        managed = spec["agent"]["boundary"]["managed"]
        for name in managed:
            assert name in testbed["devices"], f"Managed device {name} missing from testbed"

    def test_testbed_credentials_from_env(self, spec: dict) -> None:
        """Testbed credentials must reference env vars, not hardcoded."""
        from scripts.generate_testbed import generate_testbed

        testbed = generate_testbed(spec)
        default_creds = testbed["testbed"]["credentials"]["default"]
        assert "%ENV{" in default_creds["username"]
        assert "%ENV{" in default_creds["password"]

    def test_testbed_has_mgmt_ips(self, spec: dict) -> None:
        """Every device in testbed must have a management IP."""
        from scripts.generate_testbed import generate_testbed

        testbed = generate_testbed(spec)
        for name, dev in testbed["devices"].items():
            ip = dev["connections"]["cli"]["ip"]
            assert ip, f"{name} has no management IP"
            assert ip.startswith("192.168.68."), f"{name} IP {ip} not in mgmt range"
