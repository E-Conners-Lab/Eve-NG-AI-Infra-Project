"""Tests for the evpn-vni-mapping-missing scenario — TDD.

Fault: `no vxlan vlan 200 vni 10200` removed from interface Vxlan1 on
dc-leaf-2. dc-host-2 silently disappears from the EVPN data plane.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# `show vxlan vni` on Arista — both mappings present
VXLAN_HEALTHY = """\
VNI to VLAN Mapping for Vxlan1
VNI         VLAN       Source       Interface       802.1Q Tag
10100       100        static       Ethernet5       100
10200       200        static       Ethernet5       200
"""

# After fault: only VNI 10100 remains
VXLAN_MISSING = """\
VNI to VLAN Mapping for Vxlan1
VNI         VLAN       Source       Interface       802.1Q Tag
10100       100        static       Ethernet5       100
"""


def _scenario():
    from troubleshooting.scenarios.evpn_vni_mapping_missing import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_leaf_2(self) -> None:
        s = _scenario()
        assert s.device == "dc-leaf-2"
        assert s.platform == "arista_eos"
        assert s.difficulty == "advanced"


class TestInject:
    def test_inject_removes_vni_mapping(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "interface Vxlan1" in joined
        assert "no vxlan vlan 200 vni 10200" in joined


class TestDetect:
    def test_detect_returns_true_when_vni_missing(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = VXLAN_MISSING
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "10200" in evidence

    def test_detect_returns_false_when_both_vnis_present(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = VXLAN_HEALTHY
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_mapping(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "interface Vxlan1" in joined
        assert "vxlan vlan 200 vni 10200" in joined
        # Must NOT contain "no" before that line
        assert "no vxlan vlan 200 vni 10200" not in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("evpn-vni-mapping-missing").device == "dc-leaf-2"
