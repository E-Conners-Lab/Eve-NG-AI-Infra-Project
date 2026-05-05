"""Tests for the evpn-rr-peer-missing scenario — TDD.

Fault: on dc-spine-1, dc-leaf-2's EVPN session is deactivated (the
underlay session is still up, but the EVPN address-family no longer
includes it). The spine-as-RR no longer reflects dc-leaf-2's Type-2/3
routes to other leaves. dc-host-2 disappears from EVPN visibility on
remote leaves only — local L2 still works.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# `show bgp evpn summary` — leaf-2 missing or not Established
EVPN_SUMMARY_BROKEN = """\
BGP router identifier 10.1.0.1, local AS number 65000
Neighbor    V    AS    MsgRcvd    MsgSent    State/PfxRcd
10.1.0.11   4    65001 412        388        15
10.1.0.13   4    65003 401        377        4
10.1.0.14   4    65004 405        377        4
"""

EVPN_SUMMARY_HEALTHY = """\
BGP router identifier 10.1.0.1, local AS number 65000
Neighbor    V    AS    MsgRcvd    MsgSent    State/PfxRcd
10.1.0.11   4    65001 412        388        15
10.1.0.12   4    65002 410        388        15
10.1.0.13   4    65003 401        377        4
10.1.0.14   4    65004 405        377        4
"""


def _scenario():
    from troubleshooting.scenarios.evpn_rr_peer_missing import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_spine_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-spine-1"
        assert s.platform == "arista_eos"
        assert s.difficulty == "advanced"


class TestInject:
    def test_inject_deactivates_leaf_2_in_evpn_af(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "router bgp 65000" in joined
        assert "address-family evpn" in joined
        assert "no neighbor 10.1.0.12 activate" in joined


class TestDetect:
    def test_detect_returns_true_when_leaf_2_missing(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = EVPN_SUMMARY_BROKEN
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "10.1.0.12" in evidence

    def test_detect_returns_false_when_all_peers_present(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = EVPN_SUMMARY_HEALTHY
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_reactivates_leaf_2(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "router bgp 65000" in joined
        assert "address-family evpn" in joined
        assert "neighbor 10.1.0.12 activate" in joined
        # Must NOT contain "no" before activate
        lines_with_activate = [
            line.strip() for line in joined.splitlines() if "10.1.0.12 activate" in line
        ]
        assert any(not line.startswith("no") for line in lines_with_activate)


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("evpn-rr-peer-missing").device == "dc-spine-1"
