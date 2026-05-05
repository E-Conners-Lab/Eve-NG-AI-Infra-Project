"""Tests for the underlay-mtu-mismatch scenario — TDD.

Fault: dc-leaf-1 Ethernet1 (uplink to dc-spine-1) has MTU 1400 set. Small
packets pass — keepalives, BGP, ICMP at default size — but VXLAN-encapped
1500-byte payloads (1500 + 50 outer = ~1550 on the wire) get dropped.
Classic PMTUD black hole; data plane is asymmetrically broken.
"""

from __future__ import annotations

from unittest.mock import MagicMock

# Arista `show interfaces Ethernet1`-style snippets focused on MTU
INTERFACES_MTU_BAD = """\
Ethernet1 is up, line protocol is up (connected)
  Hardware is Ethernet, address is 5004.aaaa.0001
  Internet address is 10.1.1.1/31
  IP MTU 1400 bytes
  Full-duplex, 1Gb/s, link type is auto, auto-negotiation: off
"""

INTERFACES_MTU_OK = """\
Ethernet1 is up, line protocol is up (connected)
  Hardware is Ethernet, address is 5004.aaaa.0001
  Internet address is 10.1.1.1/31
  IP MTU 9214 bytes
  Full-duplex, 1Gb/s, link type is auto, auto-negotiation: off
"""


def _scenario():
    from troubleshooting.scenarios.underlay_mtu_mismatch import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_leaf_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-leaf-1"
        assert s.platform == "arista_eos"


class TestInject:
    def test_inject_sets_low_mtu(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "interface Ethernet1" in joined
        assert "mtu 1400" in joined


class TestDetect:
    def test_detect_returns_true_when_mtu_low(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = INTERFACES_MTU_BAD
        present, evidence = _scenario().detect(conn)
        assert present is True
        assert "1400" in evidence

    def test_detect_returns_false_when_mtu_jumbo(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = INTERFACES_MTU_OK
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_jumbo_mtu(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        cmds = conn.send_config_set.call_args[0][0]
        joined = "\n".join(cmds)
        assert "interface Ethernet1" in joined
        # Either explicit jumbo or `no mtu` to restore default
        assert "mtu 9214" in joined or "no mtu" in joined


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("underlay-mtu-mismatch").device == "dc-leaf-1"
