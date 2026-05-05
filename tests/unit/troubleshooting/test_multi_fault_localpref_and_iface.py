"""Tests for the multi-fault-localpref-and-iface scenario — TDD.

Composition: inject the local-pref reversal on dc-ce-1 *and* shut Et1 on
dc-border-1. The interface flap is the loud red herring; the local-pref
inversion is the cause of the user-reported symptom (DC traffic exiting
the wrong PE). The test of skill is not stopping after fixing the first
visible thing.

The CLI hands the scenario a connection to the "primary" device
(dc-ce-1). The scenario opens a side connection to dc-border-1 for the
secondary fault.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _scenario():
    from troubleshooting.scenarios.multi_fault_localpref_and_iface import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_primary_device_is_dc_ce_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-ce-1"
        assert s.platform == "cisco_iosxe"
        assert s.difficulty == "advanced"


class TestInject:
    @patch("troubleshooting.scenarios.multi_fault_localpref_and_iface.connect_device")
    def test_inject_runs_both_underlying_injects(self, mock_connect: MagicMock) -> None:
        primary_conn = MagicMock()
        border_conn = MagicMock()
        mock_connect.return_value = border_conn

        _scenario().inject(primary_conn)

        # local-pref inject runs against the primary conn (dc-ce-1)
        primary_cmds: list[str] = []
        for call in primary_conn.send_config_set.call_args_list:
            primary_cmds.extend(call[0][0])
        joined_primary = "\n".join(primary_cmds)
        assert "route-map PRIMARY-PE" in joined_primary

        # iface-admin-down inject runs against the border conn
        mock_connect.assert_called_once_with("dc-border-1", platform="arista_eos")
        border_cmds: list[str] = []
        for call in border_conn.send_config_set.call_args_list:
            border_cmds.extend(call[0][0])
        joined_border = "\n".join(border_cmds)
        assert "interface Ethernet1" in joined_border
        assert "shutdown" in joined_border

        # Side connection must be closed
        border_conn.disconnect.assert_called_once()


class TestDetect:
    @patch("troubleshooting.scenarios.multi_fault_localpref_and_iface.connect_device")
    def test_detect_returns_true_when_either_fault_present(self, mock_connect: MagicMock) -> None:
        # Primary path is the BGP localpref check; secondary is iface status
        primary_conn = MagicMock()
        primary_conn.send_command.return_value = (
            # localpref reversed output — primary path has 100, secondary 200
            "BGP routing table entry for 10.20.0.1/32, version 9\n"
            "  Refresh Epoch 5\n"
            "  64500 65120\n"
            "    172.16.0.3 from 172.16.0.3 (172.16.0.112)\n"
            "      Origin IGP, localpref 200, valid, external, best\n"
            "  Refresh Epoch 4\n"
            "  64500 65120\n"
            "    172.16.0.1 from 172.16.0.1 (172.16.0.111)\n"
            "      Origin IGP, localpref 100, valid, external\n"
        )
        border_conn = MagicMock()
        border_conn.send_command.return_value = (
            "Port       Name              Status       Vlan       Duplex  Speed   Type\n"
            "Et1        to dc-spine-1     connected    routed     full    1G      EbraTestPhyPort\n"
        )
        mock_connect.return_value = border_conn

        present, evidence = _scenario().detect(primary_conn)
        assert present is True
        # Evidence should mention at least one fault
        assert evidence  # non-empty

    @patch("troubleshooting.scenarios.multi_fault_localpref_and_iface.connect_device")
    def test_detect_returns_false_only_when_both_clean(self, mock_connect: MagicMock) -> None:
        primary_conn = MagicMock()
        primary_conn.send_command.return_value = (
            "BGP routing table entry for 10.20.0.1/32, version 8\n"
            "  Refresh Epoch 5\n"
            "  64500 65120\n"
            "    172.16.0.3 from 172.16.0.3 (172.16.0.112)\n"
            "      Origin IGP, localpref 100, valid, external\n"
            "  Refresh Epoch 4\n"
            "  64500 65120\n"
            "    172.16.0.1 from 172.16.0.1 (172.16.0.111)\n"
            "      Origin IGP, localpref 200, valid, external, best\n"
        )
        border_conn = MagicMock()
        border_conn.send_command.return_value = (
            "Port       Name              Status       Vlan       Duplex  Speed   Type\n"
            "Et1        to dc-spine-1     connected    routed     full    1G      EbraTestPhyPort\n"
        )
        mock_connect.return_value = border_conn

        present, evidence = _scenario().detect(primary_conn)
        assert present is False


class TestFix:
    @patch("troubleshooting.scenarios.multi_fault_localpref_and_iface.connect_device")
    def test_fix_runs_both_underlying_fixes(self, mock_connect: MagicMock) -> None:
        primary_conn = MagicMock()
        border_conn = MagicMock()
        mock_connect.return_value = border_conn

        _scenario().fix(primary_conn)

        primary_cmds: list[str] = []
        for call in primary_conn.send_config_set.call_args_list:
            primary_cmds.extend(call[0][0])
        joined_primary = "\n".join(primary_cmds)
        assert "set local-preference 200" in joined_primary

        border_cmds: list[str] = []
        for call in border_conn.send_config_set.call_args_list:
            border_cmds.extend(call[0][0])
        joined_border = "\n".join(border_cmds)
        assert "no shutdown" in joined_border

        border_conn.disconnect.assert_called_once()


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("multi-fault-localpref-and-iface").device == "dc-ce-1"
