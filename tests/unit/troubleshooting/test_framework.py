"""Unit tests for the troubleshooting framework — TDD.

Written before the implementation. Validates the Scenario dataclass, the
in-memory registry, and the connect/restore helpers. No live devices.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_scenario(name: str = "demo", **overrides):
    """Construct a minimal Scenario for tests."""
    from troubleshooting._common import Scenario

    defaults = dict(
        name=name,
        device="dc-ce-1",
        platform="cisco_iosxe",
        difficulty="beginner",
        summary="demo summary",
        symptoms="demo symptoms",
        runbook="runbooks/demo.md",
        inject=lambda conn: None,
        detect=lambda conn: (False, "clean"),
        fix=lambda conn: None,
    )
    defaults.update(overrides)
    return Scenario(**defaults)


class TestScenarioDataclass:
    """Scenario must capture the metadata + three behaviour hooks."""

    def test_scenario_exposes_required_fields(self) -> None:
        s = _make_scenario(name="x")
        assert s.name == "x"
        assert s.device == "dc-ce-1"
        assert s.platform == "cisco_iosxe"
        assert callable(s.inject)
        assert callable(s.detect)
        assert callable(s.fix)

    def test_difficulty_must_be_recognised(self) -> None:
        from troubleshooting._common import VALID_DIFFICULTIES

        assert "beginner" in VALID_DIFFICULTIES
        assert "intermediate" in VALID_DIFFICULTIES
        assert "advanced" in VALID_DIFFICULTIES


class TestRegistry:
    """Registry maps scenario name -> Scenario, fails loudly on duplicates."""

    def test_register_then_lookup(self) -> None:
        from troubleshooting._common import Registry

        r = Registry()
        s = _make_scenario(name="alpha")
        r.register(s)
        assert r.get("alpha") is s

    def test_lookup_unknown_raises(self) -> None:
        from troubleshooting._common import Registry

        r = Registry()
        with pytest.raises(KeyError):
            r.get("does-not-exist")

    def test_duplicate_registration_raises(self) -> None:
        from troubleshooting._common import Registry

        r = Registry()
        r.register(_make_scenario(name="dupe"))
        with pytest.raises(ValueError):
            r.register(_make_scenario(name="dupe"))

    def test_list_returns_all_in_insertion_order(self) -> None:
        from troubleshooting._common import Registry

        r = Registry()
        r.register(_make_scenario(name="b"))
        r.register(_make_scenario(name="a"))
        names = [s.name for s in r.all()]
        assert names == ["b", "a"]


class TestConnectDevice:
    """connect_device must look up mgmt IP + creds and call netmiko correctly."""

    @patch("troubleshooting._common.ConnectHandler")
    @patch("troubleshooting._common.get_mgmt_ips", return_value={"dc-ce-1": "192.168.68.120"})
    @patch("troubleshooting._common.load_credentials")
    def test_connect_uses_mgmt_ip_and_creds(
        self, mock_creds: MagicMock, mock_ips: MagicMock, mock_conn: MagicMock
    ) -> None:
        from troubleshooting._common import connect_device

        mock_creds.return_value = MagicMock(
            device_username="admin",
            device_password="lab",
            fortigate_username="",
            fortigate_password="",
        )
        connect_device("dc-ce-1", platform="cisco_iosxe")
        kwargs = mock_conn.call_args.kwargs
        assert kwargs["host"] == "192.168.68.120"
        assert kwargs["username"] == "admin"
        assert kwargs["device_type"] == "cisco_xe"

    @patch("troubleshooting._common.ConnectHandler")
    @patch("troubleshooting._common.get_mgmt_ips", return_value={"dc-fw-1": "192.168.68.140"})
    @patch("troubleshooting._common.load_credentials")
    def test_fortigate_uses_fortigate_creds(
        self, mock_creds: MagicMock, mock_ips: MagicMock, mock_conn: MagicMock
    ) -> None:
        from troubleshooting._common import connect_device

        mock_creds.return_value = MagicMock(
            device_username="admin",
            device_password="lab",
            fortigate_username="fgt-admin",
            fortigate_password="fgt-pw",
        )
        connect_device("dc-fw-1", platform="fortinet_fortios")
        kwargs = mock_conn.call_args.kwargs
        assert kwargs["username"] == "fgt-admin"
        assert kwargs["password"] == "fgt-pw"
        assert kwargs["device_type"] == "fortinet"

    @patch("troubleshooting._common.get_mgmt_ips", return_value={})
    def test_unknown_device_raises(self, mock_ips: MagicMock) -> None:
        from troubleshooting._common import connect_device

        with pytest.raises(KeyError):
            connect_device("not-a-device", platform="cisco_iosxe")


class TestRestoreCleanConfig:
    """restore_clean_config delegates to push_configs.push_config_to_device."""

    @patch("scripts.push_configs.push_config_to_device", return_value=True)
    @patch("troubleshooting._common.get_mgmt_ips", return_value={"dc-ce-1": "192.168.68.120"})
    @patch("troubleshooting._common.load_credentials")
    def test_restore_calls_push_with_clean_config(
        self, mock_creds: MagicMock, mock_ips: MagicMock, mock_push: MagicMock, tmp_path
    ) -> None:
        from troubleshooting import _common
        from troubleshooting._common import restore_clean_config

        mock_creds.return_value = MagicMock(
            device_username="admin",
            device_password="lab",
            fortigate_username="",
            fortigate_password="",
        )
        # Point CONFIGS_DIR at a temp dir with a stub config so the path check passes
        cfg = tmp_path / "dc-ce-1.cfg"
        cfg.write_text("hostname dc-ce-1\n")
        with patch.object(_common, "CONFIGS_DIR", tmp_path):
            ok = restore_clean_config("dc-ce-1", platform="cisco_iosxe")
        assert ok is True
        mock_push.assert_called_once()
