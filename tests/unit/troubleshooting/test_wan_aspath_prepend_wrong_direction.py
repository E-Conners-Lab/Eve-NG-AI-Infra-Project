"""Tests for the wan-aspath-prepend-wrong-direction scenario — TDD.

Fault: dr-ce-1 has LONG-PATH-OUT bound to the *primary* PE outbound
(172.16.0.10) instead of the secondary (172.16.0.12). Inbound traffic from
the SP cloud now prefers the *secondary* path because the primary's
advertisement is artificially long. Asymmetric routing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# `show running-config | section router bgp` excerpts
RUNCFG_REVERSED = """\
router bgp 65130
 neighbor 172.16.0.10 remote-as 64500
 neighbor 172.16.0.12 remote-as 64500
 address-family ipv4
  neighbor 172.16.0.10 activate
  neighbor 172.16.0.10 route-map PRIMARY-PE in
  neighbor 172.16.0.10 route-map LONG-PATH-OUT out
  neighbor 172.16.0.12 activate
  neighbor 172.16.0.12 route-map SECONDARY-PE in
"""

RUNCFG_CORRECT = """\
router bgp 65130
 neighbor 172.16.0.10 remote-as 64500
 neighbor 172.16.0.12 remote-as 64500
 address-family ipv4
  neighbor 172.16.0.10 activate
  neighbor 172.16.0.10 route-map PRIMARY-PE in
  neighbor 172.16.0.12 activate
  neighbor 172.16.0.12 route-map SECONDARY-PE in
  neighbor 172.16.0.12 route-map LONG-PATH-OUT out
"""


def _scenario():
    from troubleshooting.scenarios.wan_aspath_prepend_wrong_direction import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dr_ce_1(self) -> None:
        s = _scenario()
        assert s.device == "dr-ce-1"
        assert s.platform == "cisco_iosxe"


class TestInject:
    def test_inject_swaps_route_map_binding(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        all_cmds: list[str] = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        # Removes from secondary (172.16.0.12), adds to primary (172.16.0.10)
        assert "no neighbor 172.16.0.12 route-map LONG-PATH-OUT out" in joined
        assert "neighbor 172.16.0.10 route-map LONG-PATH-OUT out" in joined


class TestDetect:
    def test_detect_returns_true_when_bound_to_primary(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = RUNCFG_REVERSED
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "172.16.0.10" in evidence

    def test_detect_returns_false_when_bound_to_secondary(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = RUNCFG_CORRECT
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_secondary_binding(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        all_cmds: list[str] = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "no neighbor 172.16.0.10 route-map LONG-PATH-OUT out" in joined
        assert "neighbor 172.16.0.12 route-map LONG-PATH-OUT out" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("wan-aspath-prepend-wrong-direction").device == "dr-ce-1"
