"""Tests for the l1-iface-description-swap scenario — TDD.

Fault: descriptions on Et1 and Et2 of dc-border-1 are swapped. Et1
("to dc-spine-1") becomes "to dc-spine-2" and vice versa. The link to
spine-1 is still physically Et1 — but a glance at `show interfaces
description` lies about it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

DESC_OUTPUT_CORRECT = """\
Interface                      Status         Protocol           Description
Et1                            up             up                 to dc-spine-1
Et2                            up             up                 to dc-spine-2
Et3                            up             up                 to dc-fw-1
"""

DESC_OUTPUT_SWAPPED = """\
Interface                      Status         Protocol           Description
Et1                            up             up                 to dc-spine-2
Et2                            up             up                 to dc-spine-1
Et3                            up             up                 to dc-fw-1
"""


def _scenario():
    from troubleshooting.scenarios.l1_iface_description_swap import SCENARIO

    return SCENARIO


class TestMetadata:
    def test_targets_dc_border_1(self) -> None:
        s = _scenario()
        assert s.device == "dc-border-1"
        assert s.platform == "arista_eos"


class TestInject:
    def test_inject_swaps_descriptions(self) -> None:
        conn = MagicMock()
        _scenario().inject(conn)
        all_cmds: list[str] = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        assert "interface Ethernet1" in joined
        assert "interface Ethernet2" in joined
        assert "description to dc-spine-2" in joined  # on Et1 now
        assert "description to dc-spine-1" in joined  # on Et2 now


class TestDetect:
    def test_detect_returns_true_when_swapped(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = DESC_OUTPUT_SWAPPED
        present, evidence = _scenario().detect(conn)
        assert present is True

    def test_detect_returns_false_when_correct(self) -> None:
        conn = MagicMock()
        conn.send_command.return_value = DESC_OUTPUT_CORRECT
        present, evidence = _scenario().detect(conn)
        assert present is False


class TestFix:
    def test_fix_restores_correct_descriptions(self) -> None:
        conn = MagicMock()
        _scenario().fix(conn)
        all_cmds: list[str] = []
        for call in conn.send_config_set.call_args_list:
            all_cmds.extend(call[0][0])
        joined = "\n".join(all_cmds)
        # After fix: Et1 -> spine-1, Et2 -> spine-2
        assert "interface Ethernet1\ndescription to dc-spine-1" in joined.replace(" ", " ")


class TestRegistration:
    def test_scenario_is_registered(self) -> None:
        import troubleshooting.scenarios  # noqa: F401
        from troubleshooting._common import REGISTRY

        assert REGISTRY.get("l1-iface-description-swap").device == "dc-border-1"
