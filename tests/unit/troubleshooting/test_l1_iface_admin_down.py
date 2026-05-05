"""Tests for the l1-iface-admin-down scenario — TDD.

Scenario: shut Ethernet1 on dc-border-1 (uplink to dc-spine-1). Detect by
parsing `show interfaces Ethernet1 status` for the 'disabled' column.
"""

from __future__ import annotations

from unittest.mock import MagicMock

INTERFACE_UP_OUTPUT = """\
Port       Name              Status       Vlan       Duplex  Speed   Type
Et1        to dc-spine-1     connected    routed     full    1G      EbraTestPhyPort
"""

INTERFACE_ADMIN_DOWN_OUTPUT = """\
Port       Name              Status       Vlan       Duplex  Speed   Type
Et1        to dc-spine-1     disabled     routed     full    1G      EbraTestPhyPort
"""


def _scenario():
    from troubleshooting.scenarios.l1_iface_admin_down import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_border_1(self) -> None:
        assert _scenario().device == "dc-border-1"
        assert _scenario().platform == "arista_eos"
        assert _scenario().difficulty == "beginner"


class TestInject:
    def test_inject_shuts_ethernet1(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds = conn.send_config_set.call_args[0][0]
        assert "interface Ethernet1" in cmds
        assert "shutdown" in cmds


class TestDetect:
    def test_detect_returns_true_when_disabled(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = INTERFACE_ADMIN_DOWN_OUTPUT
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "disabled" in evidence.lower() or "down" in evidence.lower()

    def test_detect_returns_false_when_up(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = INTERFACE_UP_OUTPUT
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_unshuts_ethernet1(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        cmds = conn.send_config_set.call_args[0][0]
        assert "interface Ethernet1" in cmds
        assert "no shutdown" in cmds


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        # Importing the scenarios package registers everything
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("l1-iface-admin-down").device == "dc-border-1"
