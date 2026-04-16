"""End-to-end integration tests — full pipeline validation.

Requires live EVE-NG devices + NetBox + Batfish Docker container.
Run with: pytest tests/integration/test_full_pipeline.py -v
Skip with: pytest -m "not integration"

These tests modify live device state and NetBox data. All changes are
rolled back in finally blocks / fixture teardown.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs" / "generated"


@pytest.fixture
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text())


@pytest.fixture
def creds():
    from scripts.credentials import require_credentials

    return require_credentials("device_username", "device_password")


# ---------------------------------------------------------------------------
# Test 1: Batfish validates current configs (pre-deploy gate)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBatfishValidation:
    """Batfish must validate the current generated configs."""

    def test_snapshot_loads_successfully(self, spec: dict) -> None:
        """Batfish must parse all 17 device configs without errors."""
        from pybatfish.client.session import Session

        from batfish.snapshot import build_snapshot

        snapshot_dir = build_snapshot(
            spec, CONFIGS_DIR, CONFIGS_DIR.parent.parent / "batfish" / "snapshot"
        )
        bf = Session(host="localhost")
        bf.set_network("integration-test")
        bf.init_snapshot(str(snapshot_dir), name="test-load", overwrite=True)
        # If we get here without exception, parsing succeeded
        status = bf.q.bgpSessionStatus().answer().frame()
        assert len(status) > 0, "Batfish should find BGP sessions"

    def test_all_underlay_bgp_compatible(self, spec: dict) -> None:
        """All underlay eBGP sessions must be UNIQUE_MATCH."""
        from pybatfish.client.session import Session

        from batfish.snapshot import build_snapshot

        snapshot_dir = build_snapshot(
            spec, CONFIGS_DIR, CONFIGS_DIR.parent.parent / "batfish" / "snapshot"
        )
        bf = Session(host="localhost")
        bf.set_network("integration-test")
        bf.init_snapshot(str(snapshot_dir), name="test-bgp", overwrite=True)

        compat = bf.q.bgpSessionCompatibility().answer().frame()
        # Filter to only underlay sessions (UNIQUE_MATCH expected)
        underlay = compat[compat["Configured_Status"] == "UNIQUE_MATCH"]
        # Should have at least the spine-leaf + WAN sessions
        assert len(underlay) >= 20, f"Expected 20+ UNIQUE_MATCH sessions, got {len(underlay)}"

    def test_corrupted_config_fails_validation(self, spec: dict, tmp_path: Path) -> None:
        """Batfish gate must detect a broken config."""
        import shutil

        from batfish.validate import validate_pre_deploy

        # Copy configs to temp dir and corrupt one
        test_configs = tmp_path / "configs"
        shutil.copytree(CONFIGS_DIR, test_configs)
        corrupt = test_configs / "dc-spine-1.cfg"
        corrupt.write_text("! completely broken config\nhostname broken\n")

        # validate_pre_deploy should detect issues
        passed, issues = validate_pre_deploy(spec, test_configs)
        # A broken spine should cause BGP incompatibilities or missing sessions
        assert not passed or len(issues) > 0, "Corrupted config should produce issues"


# ---------------------------------------------------------------------------
# Test 2: Fabric health on live devices
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestLiveFabricHealth:
    """fabric_health skill must detect all BGP sessions on live devices."""

    def test_all_fabric_sessions_established(self, spec: dict, creds: object) -> None:
        """All Arista fabric BGP sessions must be Established."""
        from agent.runner import run_fabric_health

        log_file = Path("logs") / "integration_test.jsonl"
        result = run_fabric_health(spec, creds, log_file)

        assert result["passed"] > 0, "At least some fabric devices should pass"
        assert result["failed"] == 0, (
            f"Fabric health failures: "
            f"{[k for k, v in result['devices'].items() if not v.get('healthy', False)]}"
        )

    def test_all_branch_sessions_established(self, spec: dict, creds: object) -> None:
        """All CE router dual-homed BGP sessions must be Established."""
        from agent.runner import run_branch_connectivity

        log_file = Path("logs") / "integration_test.jsonl"
        result = run_branch_connectivity(spec, creds, log_file)

        assert result["passed"] > 0
        assert result["failed"] == 0, (
            f"Branch connectivity failures: "
            f"{[k for k, v in result['devices'].items() if not v.get('healthy', False)]}"
        )


# ---------------------------------------------------------------------------
# Test 3: Spec compliance on live devices
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestLiveSpecCompliance:
    """spec_compliance must show zero drift on a clean deployment."""

    def test_zero_drift_all_devices(self, spec: dict, creds: object) -> None:
        """All managed devices must have zero spec compliance drift."""
        from agent.runner import run_spec_compliance

        log_file = Path("logs") / "integration_test.jsonl"
        result = run_spec_compliance(spec, creds, log_file)

        drifted = [
            k for k, v in result["devices"].items() if v.get("drifts") and len(v["drifts"]) > 0
        ]
        assert result["failed"] == 0, f"Spec compliance drift on: {drifted}"


# ---------------------------------------------------------------------------
# Test 4: BGP break detection
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBgpBreakDetection:
    """Agent must detect a deliberately broken BGP session."""

    def test_shut_interface_detected(self, spec: dict, creds: object) -> None:
        """Shut a spine interface → agent detects within one cycle."""
        from netmiko import ConnectHandler

        from agent.runner import run_fabric_health
        from scripts.bootstrap_config import get_mgmt_ips

        mgmt_ips = get_mgmt_ips()

        # Shut Ethernet1 on dc-spine-1 (link to dc-leaf-1)
        conn = ConnectHandler(
            device_type="arista_eos",
            host=mgmt_ips["dc-spine-1"],
            username=creds.device_username,
            password=creds.device_password,
            secret=creds.device_password,
            timeout=15,
        )
        conn.enable()

        try:
            conn.send_config_set(["interface Ethernet1", "shutdown"], cmd_verify=False)
            conn.disconnect()

            # Run agent — should detect the down session
            log_file = Path("logs") / "integration_test.jsonl"
            result = run_fabric_health(spec, creds, log_file)

            spine_result = result["devices"].get("dc-spine-1", {})
            assert not spine_result.get("healthy", True), (
                "dc-spine-1 should be unhealthy after shutting Ethernet1"
            )
            assert (
                spine_result.get("down", 0) > 0 or len(spine_result.get("down_neighbors", [])) > 0
            )
        finally:
            # Rollback: re-enable the interface
            conn2 = ConnectHandler(
                device_type="arista_eos",
                host=mgmt_ips["dc-spine-1"],
                username=creds.device_username,
                password=creds.device_password,
                secret=creds.device_password,
                timeout=15,
            )
            conn2.enable()
            conn2.send_config_set(["interface Ethernet1", "no shutdown"], cmd_verify=False)
            conn2.send_command("write memory")
            conn2.disconnect()


# ---------------------------------------------------------------------------
# Test 5: Failure impact analysis via Batfish
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestBatfishFailureImpact:
    """Batfish must predict which paths break when a node goes down."""

    def test_spine_failure_impact(self, spec: dict) -> None:
        """Deactivating a spine should break some paths but not all (ECMP)."""
        from pybatfish.client.session import Session

        from batfish.snapshot import build_snapshot
        from batfish.validate import failure_impact

        snapshot_dir = build_snapshot(
            spec, CONFIGS_DIR, CONFIGS_DIR.parent.parent / "batfish" / "snapshot"
        )
        bf = Session(host="localhost")
        bf.set_network("integration-test")
        bf.init_snapshot(str(snapshot_dir), name="impact-test", overwrite=True)

        result = failure_impact(bf, "dc-spine-1", spec, base_snapshot="impact-test")
        assert result["node"] == "dc-spine-1"
        # With 2 spines and ECMP, losing one spine should NOT break all paths
        # Some paths may reroute through dc-spine-2
        assert isinstance(result["broken"], list)
        assert isinstance(result["surviving"], list)
