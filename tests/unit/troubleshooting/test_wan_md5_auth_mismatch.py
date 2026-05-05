"""Tests for the wan-md5-auth-mismatch scenario — TDD.

Fault: dc-ce-1 has `neighbor 172.16.0.1 password BADPASS` but sp-pe-1
has no password configured. TCP MD5 fails — session never establishes,
keeps churning Active/Idle.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# `show ip bgp summary` — primary stuck in Active, secondary fine
SUMMARY_BROKEN = """\
BGP router identifier 172.16.0.101, local AS number 65100
Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
172.16.0.1      4        64500       0       0        0    0    0 never    Active
172.16.0.3      4        64500     421     401       10    0    0 05:55:01        4
"""

SUMMARY_HEALTHY = """\
BGP router identifier 172.16.0.101, local AS number 65100
Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
172.16.0.1      4        64500     422     401       10    0    0 05:56:11        4
172.16.0.3      4        64500     421     401       10    0    0 05:55:01        4
"""


def _scenario():
    from troubleshooting.scenarios.wan_md5_auth_mismatch import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_ce_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-ce-1"
        assert s.platform == "cisco_iosxe"


class TestInject:
    def test_inject_sets_password(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        all_cmds: list[str] = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "router bgp 65100" in joined
        assert "neighbor 172.16.0.1 password" in joined


class TestDetect:
    def test_detect_returns_true_when_session_not_established(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = SUMMARY_BROKEN
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "172.16.0.1" in evidence

    def test_detect_returns_false_when_established(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = SUMMARY_HEALTHY
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_removes_password(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        all_cmds: list[str] = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "no neighbor 172.16.0.1 password" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("wan-md5-auth-mismatch").device == "dc-ce-1"
