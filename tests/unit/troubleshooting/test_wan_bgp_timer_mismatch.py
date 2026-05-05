"""Tests for the wan-bgp-timer-mismatch scenario — TDD.

Fault: aggressive `neighbor 172.16.0.1 timers 5 15` on dc-ce-1.
sp-pe-1 still uses default 60/180. Session keeps going Idle / OpenSent on
hold-time mismatch and reconverging.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# `show ip bgp neighbors` snippets — IOS-XE format
NEIGHBOR_OUTPUT_DEFAULT = """\
BGP neighbor is 172.16.0.1,  remote AS 64500, external link
  BGP version 4, remote router ID 172.16.0.111
  BGP state = Established, up for 01:23:45
  Last read 00:00:12, last write 00:00:11, hold time is 180, keepalive interval is 60 seconds
  Configured hold time is 180, keepalive interval is 60 seconds
"""

NEIGHBOR_OUTPUT_AGGRESSIVE = """\
BGP neighbor is 172.16.0.1,  remote AS 64500, external link
  BGP version 4, remote router ID 172.16.0.111
  BGP state = Idle
  Last read 00:00:01, last write 00:00:00, hold time is 15, keepalive interval is 5 seconds
  Configured hold time is 15, keepalive interval is 5 seconds
"""


def _scenario():
    from troubleshooting.scenarios.wan_bgp_timer_mismatch import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_ce_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-ce-1"
        assert s.platform == "cisco_iosxe"


class TestInject:
    def test_inject_sets_aggressive_timers(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        all_cmds = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "neighbor 172.16.0.1 timers" in joined
        # Aggressive ratio (5/15) is what causes the flap
        assert "5 15" in joined


class TestDetect:
    def test_detect_returns_true_when_aggressive(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = NEIGHBOR_OUTPUT_AGGRESSIVE
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "15" in evidence or "hold" in evidence.lower()

    def test_detect_returns_false_at_default(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = NEIGHBOR_OUTPUT_DEFAULT
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_removes_neighbor_timers(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        all_cmds = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "no neighbor 172.16.0.1 timers" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("wan-bgp-timer-mismatch").device == "dc-ce-1"
