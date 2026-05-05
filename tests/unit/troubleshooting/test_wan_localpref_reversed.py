"""Tests for the wan-localpref-reversed scenario — TDD.

Fault: PRIMARY-PE/SECONDARY-PE route-map values on dc-ce-1 are swapped.
The names still look right; only the `set local-preference` values are wrong.
Detect by parsing `show ip bgp 10.20.0.1` for the local-pref of each path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# Path from sp-pe-1 (172.16.0.1) carries localpref 200 — correct
BGP_OUTPUT_CORRECT = """\
BGP routing table entry for 10.20.0.1/32, version 8
Paths: (2 available, best #2, table default)
  Refresh Epoch 5
  64500 65120
    172.16.0.3 from 172.16.0.3 (172.16.0.112)
      Origin IGP, localpref 100, valid, external
  Refresh Epoch 4
  64500 65120
    172.16.0.1 from 172.16.0.1 (172.16.0.111)
      Origin IGP, localpref 200, valid, external, best
"""

# Reversed: sp-pe-1 path has localpref 100, sp-pe-2 path has 200
BGP_OUTPUT_REVERSED = """\
BGP routing table entry for 10.20.0.1/32, version 9
Paths: (2 available, best #1, table default)
  Refresh Epoch 5
  64500 65120
    172.16.0.3 from 172.16.0.3 (172.16.0.112)
      Origin IGP, localpref 200, valid, external, best
  Refresh Epoch 4
  64500 65120
    172.16.0.1 from 172.16.0.1 (172.16.0.111)
      Origin IGP, localpref 100, valid, external
"""


def _scenario():
    from troubleshooting.scenarios.wan_localpref_reversed import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_ce_1_intermediate(self) -> None:
        s = _scenario()
        assert s.device == "dc-ce-1"
        assert s.platform == "cisco_iosxe"
        assert s.difficulty == "intermediate"


class TestInject:
    def test_inject_swaps_localpref_values(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        # All config commands flattened across all calls (handles both single and chained sends)
        all_cmds = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        # PRIMARY-PE must end up at 100, SECONDARY-PE at 200
        assert "route-map PRIMARY-PE" in joined
        assert "route-map SECONDARY-PE" in joined
        assert "set local-preference 100" in joined
        assert "set local-preference 200" in joined

    def test_inject_triggers_route_refresh_in_on_both_pes(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds_seen = " ".join(
            str(c) for c in conn.send_command.call_args_list + conn.send_config_set.call_args_list
        )
        assert "172.16.0.1" in cmds_seen
        assert "172.16.0.3" in cmds_seen
        assert "soft" in cmds_seen.lower()


class TestDetect:
    def test_detect_returns_true_when_reversed(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = BGP_OUTPUT_REVERSED
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "172.16.0.1" in evidence or "primary" in evidence.lower()

    def test_detect_returns_false_when_correct(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = BGP_OUTPUT_CORRECT
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_correct_localpref_values(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        all_cmds = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        # PRIMARY-PE back to 200, SECONDARY-PE back to 100
        assert "route-map PRIMARY-PE" in joined
        assert "set local-preference 200" in joined
        assert "route-map SECONDARY-PE" in joined
        assert "set local-preference 100" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("wan-localpref-reversed").device == "dc-ce-1"
