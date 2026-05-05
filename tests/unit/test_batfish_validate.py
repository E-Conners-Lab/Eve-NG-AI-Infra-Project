"""Tests for Batfish validation queries — TDD with mocked pybatfish.

Written BEFORE the implementation. All pybatfish interactions are mocked
so these tests run without a Batfish server.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"


@pytest.fixture
def spec() -> dict:
    """Load the full lab spec."""
    return yaml.safe_load(SPEC_PATH.read_text())


@pytest.fixture
def mock_bf_session() -> MagicMock:
    """Create a mocked pybatfish Session with common query responses."""
    session = MagicMock()

    # bgpSessionCompatibility — all sessions compatible
    compat_df = pd.DataFrame(
        {
            "Node": ["dc-spine-1", "dc-spine-1", "dc-leaf-1"],
            "VRF": ["default", "default", "default"],
            "Local_IP": ["10.1.1.0", "10.1.1.2", "10.1.1.1"],
            "Remote_IP": ["10.1.1.1", "10.1.1.3", "10.1.1.0"],
            "Remote_AS": [65001, 65002, 65000],
            "Configured_Status": ["UNIQUE_MATCH", "UNIQUE_MATCH", "UNIQUE_MATCH"],
        }
    )
    session.q.bgpSessionCompatibility.return_value.answer.return_value.frame.return_value = (
        compat_df
    )

    # bgpSessionStatus — all established
    status_df = pd.DataFrame(
        {
            "Node": ["dc-spine-1", "dc-spine-1", "dc-leaf-1"],
            "VRF": ["default", "default", "default"],
            "Local_IP": ["10.1.1.0", "10.1.1.2", "10.1.1.1"],
            "Remote_IP": ["10.1.1.1", "10.1.1.3", "10.1.1.0"],
            "Established_Status": ["ESTABLISHED", "ESTABLISHED", "ESTABLISHED"],
        }
    )
    session.q.bgpSessionStatus.return_value.answer.return_value.frame.return_value = status_df

    return session


# ---------------------------------------------------------------------------
# BGP session checks
# ---------------------------------------------------------------------------
class TestCheckBgpSessions:
    """check_bgp_sessions must identify incompatible BGP peers."""

    def test_all_compatible_returns_empty(self, mock_bf_session: MagicMock) -> None:
        """No issues when all sessions are UNIQUE_MATCH."""
        from batfish.validate import check_bgp_sessions

        issues = check_bgp_sessions(mock_bf_session)
        assert issues == []

    def test_incompatible_session_returned(self, mock_bf_session: MagicMock) -> None:
        """Incompatible sessions must be returned with details."""
        from batfish.validate import check_bgp_sessions

        # Override with an incompatible session
        bad_df = pd.DataFrame(
            {
                "Node": ["dc-spine-1", "dc-leaf-1"],
                "VRF": ["default", "default"],
                "Local_IP": ["10.1.1.0", "10.1.1.1"],
                "Remote_IP": ["10.1.1.1", "10.1.1.0"],
                "Remote_AS": [65001, 65000],
                "Configured_Status": ["UNIQUE_MATCH", "NO_REMOTE_AS"],
            }
        )
        compat_mock = mock_bf_session.q.bgpSessionCompatibility.return_value
        compat_mock.answer.return_value.frame.return_value = bad_df
        issues = check_bgp_sessions(mock_bf_session)
        assert len(issues) == 1
        assert issues[0]["node"] == "dc-leaf-1"
        assert issues[0]["status"] == "NO_REMOTE_AS"

    def test_returns_all_incompatible(self, mock_bf_session: MagicMock) -> None:
        """Must return ALL incompatible sessions, not just the first."""
        from batfish.validate import check_bgp_sessions

        bad_df = pd.DataFrame(
            {
                "Node": ["dc-spine-1", "dc-leaf-1", "dc-leaf-2"],
                "VRF": ["default", "default", "default"],
                "Local_IP": ["10.1.1.0", "10.1.1.1", "10.1.1.3"],
                "Remote_IP": ["10.1.1.1", "10.1.1.0", "10.1.1.2"],
                "Remote_AS": [65001, 65000, 65000],
                "Configured_Status": [
                    "UNKNOWN_REMOTE",
                    "NO_REMOTE_AS",
                    "LOCAL_IP_UNKNOWN_STATICALLY",
                ],
            }
        )
        compat_mock = mock_bf_session.q.bgpSessionCompatibility.return_value
        compat_mock.answer.return_value.frame.return_value = bad_df
        issues = check_bgp_sessions(mock_bf_session)
        assert len(issues) == 3


# ---------------------------------------------------------------------------
# Reachability checks
# ---------------------------------------------------------------------------
class TestCheckReachability:
    """check_reachability must run traceroute and report reachable/unreachable."""

    def test_reachable_path(self, mock_bf_session: MagicMock) -> None:
        """Reachable destination returns reachable=True with hops."""
        from batfish.validate import check_reachability

        trace_df = pd.DataFrame(
            {
                "Flow": ["start->dst"],
                "Traces": [
                    [
                        MagicMock(
                            disposition="ACCEPTED",
                            hops=[MagicMock(), MagicMock()],
                        )
                    ]
                ],
                "TraceCount": [1],
            }
        )
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = trace_df
        result = check_reachability(mock_bf_session, "10.20.1.10", "10.10.1.10")
        assert result["reachable"] is True
        assert result["disposition"] == "ACCEPTED"

    def test_unreachable_path(self, mock_bf_session: MagicMock) -> None:
        """Unreachable destination returns reachable=False."""
        from batfish.validate import check_reachability

        trace_df = pd.DataFrame(
            {
                "Flow": ["start->dst"],
                "Traces": [[MagicMock(disposition="NO_ROUTE", hops=[])]],
                "TraceCount": [1],
            }
        )
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = trace_df
        result = check_reachability(mock_bf_session, "10.20.1.10", "10.10.1.10")
        assert result["reachable"] is False
        assert result["disposition"] == "NO_ROUTE"

    def test_empty_traces(self, mock_bf_session: MagicMock) -> None:
        """Empty trace result means unreachable."""
        from batfish.validate import check_reachability

        trace_df = pd.DataFrame({"Flow": [], "Traces": [], "TraceCount": []})
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = trace_df
        result = check_reachability(mock_bf_session, "10.20.1.10", "10.10.1.10")
        assert result["reachable"] is False


# ---------------------------------------------------------------------------
# Failure impact analysis
# ---------------------------------------------------------------------------
class TestFailureImpact:
    """failure_impact must fork snapshot and check reachability under failure."""

    def test_calls_fork_snapshot(self, mock_bf_session: MagicMock, spec: dict) -> None:
        """Must fork the snapshot with the target node deactivated."""
        from batfish.validate import failure_impact

        # Set up mock for fork + reachability
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = (
            pd.DataFrame(
                {
                    "Flow": ["f"],
                    "Traces": [[MagicMock(disposition="ACCEPTED", hops=[])]],
                    "TraceCount": [1],
                }
            )
        )

        failure_impact(mock_bf_session, "dc-spine-1", spec)
        mock_bf_session.fork_snapshot.assert_called_once()
        call_kwargs = mock_bf_session.fork_snapshot.call_args
        assert "dc-spine-1" in call_kwargs.kwargs.get(
            "deactivate_nodes", call_kwargs.args[1] if len(call_kwargs.args) > 1 else []
        )

    def test_returns_broken_paths(self, mock_bf_session: MagicMock, spec: dict) -> None:
        """Must report which reachability matrix entries break under failure."""
        from batfish.validate import failure_impact

        # All paths unreachable under failure
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = (
            pd.DataFrame(
                {
                    "Flow": ["f"],
                    "Traces": [[MagicMock(disposition="NO_ROUTE", hops=[])]],
                    "TraceCount": [1],
                }
            )
        )

        result = failure_impact(mock_bf_session, "dc-spine-1", spec)
        assert "broken" in result
        assert "surviving" in result
        assert isinstance(result["broken"], list)


# ---------------------------------------------------------------------------
# validate_pre_deploy orchestrator
# ---------------------------------------------------------------------------
class TestValidatePreDeploy:
    """validate_pre_deploy must orchestrate all checks and return pass/fail."""

    @patch("batfish.validate.init_batfish")
    def test_all_pass_returns_true(
        self, mock_init: MagicMock, mock_bf_session: MagicMock, spec: dict
    ) -> None:
        """All checks passing → (True, [])."""
        from batfish.validate import validate_pre_deploy

        mock_init.return_value = mock_bf_session

        # Traceroute returns reachable for all calls
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = (
            pd.DataFrame(
                {
                    "Flow": ["f"],
                    "Traces": [[MagicMock(disposition="ACCEPTED", hops=[])]],
                    "TraceCount": [1],
                }
            )
        )

        passed, issues = validate_pre_deploy(spec, Path("configs/generated"))
        assert passed is True

    @patch("batfish.validate.init_batfish")
    def test_bgp_incompatible_returns_false(
        self, mock_init: MagicMock, mock_bf_session: MagicMock, spec: dict
    ) -> None:
        """Any incompatible BGP session → (False, [...])."""
        from batfish.validate import validate_pre_deploy

        mock_init.return_value = mock_bf_session

        # BGP has an incompatible session
        bad_df = pd.DataFrame(
            {
                "Node": ["dc-spine-1"],
                "VRF": ["default"],
                "Local_IP": ["10.1.1.0"],
                "Remote_IP": ["10.1.1.1"],
                "Remote_AS": [65001],
                "Configured_Status": ["NO_REMOTE_AS"],
            }
        )
        compat_mock = mock_bf_session.q.bgpSessionCompatibility.return_value
        compat_mock.answer.return_value.frame.return_value = bad_df

        # Traceroute returns reachable
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = (
            pd.DataFrame(
                {
                    "Flow": ["f"],
                    "Traces": [[MagicMock(disposition="ACCEPTED", hops=[])]],
                    "TraceCount": [1],
                }
            )
        )

        passed, issues = validate_pre_deploy(spec, Path("configs/generated"))
        assert passed is False
        assert any("bgp" in str(i).lower() for i in issues)

    @patch("batfish.validate.init_batfish")
    def test_unreachable_path_is_warning_not_critical(
        self, mock_init: MagicMock, mock_bf_session: MagicMock, spec: dict
    ) -> None:
        """Unreachable host path → warning (not critical), still passes."""
        from batfish.validate import validate_pre_deploy

        mock_init.return_value = mock_bf_session

        # Traceroute returns unreachable
        mock_bf_session.q.traceroute.return_value.answer.return_value.frame.return_value = (
            pd.DataFrame(
                {
                    "Flow": ["f"],
                    "Traces": [[MagicMock(disposition="NO_ROUTE", hops=[])]],
                    "TraceCount": [1],
                }
            )
        )

        passed, issues = validate_pre_deploy(spec, Path("configs/generated"))
        # Host-to-host reachability is a warning, not critical
        assert passed is True
        assert any("reachability" in str(i).lower() for i in issues)
        assert all(
            i.get("severity") == "warning" for i in issues if "reachability" in str(i).lower()
        )

    @patch("batfish.validate.init_batfish")
    def test_connection_error_raises_clear_message(self, mock_init: MagicMock, spec: dict) -> None:
        """Batfish connection failure must raise BatfishConnectionError."""
        from batfish.validate import BatfishConnectionError, validate_pre_deploy

        mock_init.side_effect = BatfishConnectionError("Batfish server not reachable")

        with pytest.raises(BatfishConnectionError, match="not reachable"):
            validate_pre_deploy(spec, Path("configs/generated"))


# ---------------------------------------------------------------------------
# Device IP resolution from spec
# ---------------------------------------------------------------------------
class TestResolveHostIps:
    """resolve_host_ip must find first-interface IPs for reachability matrix devices."""

    def test_resolves_dc_leaf_1(self, spec: dict) -> None:
        """dc-leaf-1 must resolve to its first interface IP (10.1.1.1)."""
        from batfish.validate import resolve_host_ip

        ip = resolve_host_ip(spec, "dc-leaf-1")
        assert ip == "10.1.1.1"

    def test_resolves_br_ce_1(self, spec: dict) -> None:
        """br-ce-1 must resolve to its first interface IP (172.16.0.7)."""
        from batfish.validate import resolve_host_ip

        ip = resolve_host_ip(spec, "br-ce-1")
        assert ip == "172.16.0.7"

    def test_resolves_dr_leaf_1(self, spec: dict) -> None:
        """dr-leaf-1 must resolve to its first interface IP (10.31.1.0)."""
        from batfish.validate import resolve_host_ip

        ip = resolve_host_ip(spec, "dr-leaf-1")
        assert ip == "10.31.1.0"

    def test_unknown_device_returns_empty(self, spec: dict) -> None:
        """Unknown device must return empty string, not crash."""
        from batfish.validate import resolve_host_ip

        ip = resolve_host_ip(spec, "nonexistent-host")
        assert ip == ""
