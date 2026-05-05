"""Tests for the wan-prefix-filter-typo scenario — TDD.

Fault: an inbound prefix-list on dc-ce-1 from sp-pe-1 denies 10.20.0.0/16
(the branch supernet) where it was meant to deny something else. The
branch loopback disappears from BGP via the primary PE; only sp-pe-2
still has the path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# Both PEs advertise 10.20.0.1/32; only sp-pe-2 path remains.
BGP_OUTPUT_FILTERED = """\
BGP routing table entry for 10.20.0.1/32, version 12
Paths: (1 available, best #1, table default)
  Refresh Epoch 6
  64500 65120
    172.16.0.3 from 172.16.0.3 (172.16.0.112)
      Origin IGP, localpref 100, valid, external, best
"""

BGP_OUTPUT_HEALTHY = """\
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


def _scenario():
    from troubleshooting.scenarios.wan_prefix_filter_typo import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_ce_1(self) -> None:
        assert _scenario().device == "dc-ce-1"
        assert _scenario().platform == "cisco_iosxe"


class TestInject:
    def test_inject_creates_deny_prefix_list_and_binds_inbound(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        all_cmds = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "ip prefix-list" in joined
        assert "deny 10.20.0.0/16" in joined
        assert "neighbor 172.16.0.1 prefix-list" in joined
        assert " in" in joined  # inbound binding


class TestDetect:
    def test_detect_returns_true_when_path_via_primary_missing(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = BGP_OUTPUT_FILTERED
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "172.16.0.1" in evidence

    def test_detect_returns_false_when_both_paths_present(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = BGP_OUTPUT_HEALTHY
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_removes_inbound_filter_and_prefix_list(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        all_cmds = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "no neighbor 172.16.0.1 prefix-list" in joined
        # Also should clean up the prefix-list itself (no ip prefix-list ...)
        assert "no ip prefix-list" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("wan-prefix-filter-typo").device == "dc-ce-1"
