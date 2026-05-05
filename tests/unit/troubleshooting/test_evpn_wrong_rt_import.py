"""Tests for the evpn-wrong-rt-import scenario — TDD.

Fault: dc-leaf-2 imports VLAN 200 EVPN routes under route-target
65000:99200 instead of the fabric-wide 65000:10200. Type-2 MAC routes
for VNI 10200 arrive on dc-leaf-2 but aren't accepted into the L2 EVI,
so dc-leaf-2 doesn't learn dc-host-1's far-end MAC. Forwarding to
dc-host-1's MAC results in flooding (best case) or drops.
"""

from __future__ import annotations

from unittest.mock import MagicMock

EXPECTED_RT = "65000:10200"
WRONG_RT = "65000:99200"

RUNCFG_BAD = f"""\
router bgp 65002
   vlan 200
      rd 10.1.0.12:10200
      route-target import {WRONG_RT}
      route-target export {EXPECTED_RT}
      redistribute learned
"""

RUNCFG_OK = f"""\
router bgp 65002
   vlan 200
      rd 10.1.0.12:10200
      route-target import {EXPECTED_RT}
      route-target export {EXPECTED_RT}
      redistribute learned
"""


def _scenario():
    from troubleshooting.scenarios.evpn_wrong_rt_import import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_leaf_2(self) -> None:
        s = _scenario()
        assert s.device == "dc-leaf-2"
        assert s.platform == "arista_eos"
        assert s.difficulty == "advanced"


class TestInject:
    def test_inject_sets_wrong_import_rt(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "router bgp" in joined
        assert "vlan 200" in joined
        assert f"route-target import {WRONG_RT}" in joined
        # Must remove the old import too — Arista accepts multiple imports
        assert f"no route-target import {EXPECTED_RT}" in joined


class TestDetect:
    def test_detect_returns_true_when_wrong_rt_in_config(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = RUNCFG_BAD
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert WRONG_RT in evidence or "import" in evidence.lower()

    def test_detect_returns_false_when_rt_correct(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = RUNCFG_OK
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_correct_import_rt(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert f"route-target import {EXPECTED_RT}" in joined
        assert f"no route-target import {WRONG_RT}" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("evpn-wrong-rt-import").device == "dc-leaf-2"
