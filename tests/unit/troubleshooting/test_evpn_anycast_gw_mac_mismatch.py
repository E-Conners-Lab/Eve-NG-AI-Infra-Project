"""Tests for the evpn-anycast-gw-mac-mismatch scenario — TDD.

Fault: dc-leaf-1's `ip virtual-router mac-address` is changed to a value
that doesn't match dc-leaf-2. Both leaves act as anycast gateways for the
same SVIs, but advertise different MACs. Hosts get inconsistent gateway
MACs depending on which leaf they're behind, and ARP after a vMotion
or move stays stale.
"""

from __future__ import annotations

from unittest.mock import MagicMock

EXPECTED_MAC = "00:1c:73:00:00:01"
INJECTED_MAC = "00:1c:73:de:ad:01"

VR_OUTPUT_BAD = f"""\
Anycast MAC address: {INJECTED_MAC}
Configured on interfaces: Vlan100, Vlan200
"""

VR_OUTPUT_OK = f"""\
Anycast MAC address: {EXPECTED_MAC}
Configured on interfaces: Vlan100, Vlan200
"""


def _scenario():
    from troubleshooting.scenarios.evpn_anycast_gw_mac_mismatch import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_leaf_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-leaf-1"
        assert s.platform == "arista_eos"
        assert s.difficulty == "advanced"


class TestInject:
    def test_inject_sets_wrong_mac(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "ip virtual-router mac-address" in joined
        # Wrong MAC must appear in the inject path
        assert "de:ad" in joined.lower() or "DEAD" in joined.upper()


class TestDetect:
    def test_detect_returns_true_when_mac_wrong(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = VR_OUTPUT_BAD
        present, evidence = _scenario().detect(conn)
        assert present is True

    def test_detect_returns_false_when_mac_correct(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = VR_OUTPUT_OK
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_correct_mac(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "ip virtual-router mac-address" in joined
        assert EXPECTED_MAC in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("evpn-anycast-gw-mac-mismatch").device == "dc-leaf-1"
