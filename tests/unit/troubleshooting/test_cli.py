"""Unit tests for the troubleshooting CLI — TDD.

Each subcommand resolves a scenario from the registry and dispatches one
of inject/detect/fix/restore. Connection is mocked so tests stay offline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _stub_scenario(name: str = "demo", **overrides):
    from troubleshooting._common import Scenario

    defaults = dict(
        name=name,
        device="dc-ce-1",
        platform="cisco_iosxe",
        difficulty="beginner",
        summary="demo summary",
        symptoms="demo symptoms",
        runbook="troubleshooting/runbooks/demo.md",
        inject=MagicMock(),
        detect=MagicMock(return_value=(False, "no fault")),
        fix=MagicMock(),
    )
    defaults.update(overrides)
    return Scenario(**defaults)


@pytest.fixture
def isolated_registry(monkeypatch):
    """Replace REGISTRY with a fresh one so tests don't see real scenarios."""
    from troubleshooting import _common, cli

    fresh = _common.Registry()
    monkeypatch.setattr(_common, "REGISTRY", fresh)
    monkeypatch.setattr(cli, "REGISTRY", fresh)
    return fresh


class TestList:
    """`list` prints every registered scenario with its difficulty + device."""

    def test_list_prints_all_scenarios(self, isolated_registry, capsys) -> None:
        from troubleshooting.cli import main

        isolated_registry.register(_stub_scenario(name="alpha", difficulty="beginner"))
        isolated_registry.register(_stub_scenario(name="beta", difficulty="advanced"))

        rc = main(["list"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "alpha" in out
        assert "beta" in out
        assert "beginner" in out
        assert "advanced" in out


class TestInject:
    """`inject` opens a connection and calls the scenario's inject hook."""

    @patch("troubleshooting.cli.connect_device")
    def test_inject_dispatches_to_scenario(
        self, mock_connect: MagicMock, isolated_registry
    ) -> None:
        from troubleshooting.cli import main

        scenario = _stub_scenario(name="demo")
        isolated_registry.register(scenario)
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        rc = main(["inject", "demo"])

        assert rc == 0
        mock_connect.assert_called_once_with("dc-ce-1", platform="cisco_iosxe")
        scenario.inject.assert_called_once_with(mock_conn)
        mock_conn.disconnect.assert_called_once()

    def test_inject_unknown_scenario_returns_nonzero(self, isolated_registry, capsys) -> None:
        from troubleshooting.cli import main

        rc = main(["inject", "missing"])
        assert rc != 0
        assert "missing" in capsys.readouterr().err


class TestStatus:
    """`status` reports whether the fault is currently present (no spoilers)."""

    @patch("troubleshooting.cli.connect_device")
    def test_status_prints_fault_present(
        self, mock_connect: MagicMock, isolated_registry, capsys
    ) -> None:
        from troubleshooting.cli import main

        scenario = _stub_scenario(
            name="demo",
            detect=MagicMock(return_value=(True, "localpref 100 on primary")),
        )
        isolated_registry.register(scenario)
        mock_connect.return_value = MagicMock()

        rc = main(["status", "demo"])
        out = capsys.readouterr().out
        assert rc == 1  # nonzero so users can chain in scripts
        assert "FAULT PRESENT" in out
        assert "localpref" in out

    @patch("troubleshooting.cli.connect_device")
    def test_status_prints_clean(self, mock_connect: MagicMock, isolated_registry, capsys) -> None:
        from troubleshooting.cli import main

        scenario = _stub_scenario(
            name="demo",
            detect=MagicMock(return_value=(False, "all paths normal")),
        )
        isolated_registry.register(scenario)
        mock_connect.return_value = MagicMock()

        rc = main(["status", "demo"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "NO FAULT" in out


class TestFix:
    """`fix` applies the targeted repair (the answer key)."""

    @patch("troubleshooting.cli.connect_device")
    def test_fix_dispatches_to_scenario(self, mock_connect: MagicMock, isolated_registry) -> None:
        from troubleshooting.cli import main

        scenario = _stub_scenario(name="demo")
        isolated_registry.register(scenario)
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        rc = main(["fix", "demo"])

        assert rc == 0
        scenario.fix.assert_called_once_with(mock_conn)


class TestRestore:
    """`restore` calls restore_clean_config — the nuclear option."""

    @patch("troubleshooting.cli.restore_clean_config", return_value=True)
    def test_restore_pushes_clean_config(self, mock_restore: MagicMock, isolated_registry) -> None:
        from troubleshooting.cli import main

        isolated_registry.register(_stub_scenario(name="demo"))
        rc = main(["restore", "demo"])
        assert rc == 0
        mock_restore.assert_called_once_with("dc-ce-1", platform="cisco_iosxe")


class TestRunbook:
    """`runbook` prints the markdown solution file."""

    def test_runbook_prints_file_contents(
        self, isolated_registry, tmp_path, capsys, monkeypatch
    ) -> None:
        from troubleshooting import cli

        rb = tmp_path / "demo.md"
        rb.write_text("# demo runbook\n\nthe answer is X\n")
        scenario = _stub_scenario(name="demo", runbook=str(rb))
        isolated_registry.register(scenario)

        # Anchor relative paths to tmp_path for the test
        monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

        rc = cli.main(["runbook", "demo"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "the answer is X" in out

    def test_runbook_missing_file_returns_nonzero(
        self, isolated_registry, capsys, monkeypatch, tmp_path
    ) -> None:
        from troubleshooting import cli

        scenario = _stub_scenario(name="demo", runbook="missing.md")
        isolated_registry.register(scenario)
        monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

        rc = cli.main(["runbook", "demo"])
        assert rc != 0
        assert "missing" in capsys.readouterr().err.lower()


class TestShow:
    """`show` prints scenario metadata (device, difficulty, symptoms) without spoilers."""

    def test_show_prints_metadata(self, isolated_registry, capsys) -> None:
        from troubleshooting.cli import main

        scenario = _stub_scenario(
            name="demo",
            symptoms="branch loopback unreachable from DC",
            summary="prefix list typo",
        )
        isolated_registry.register(scenario)

        rc = main(["show", "demo"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "demo" in out
        assert "dc-ce-1" in out
        assert "branch loopback unreachable" in out
        # Solution must NOT leak in `show`
        assert "prefix list typo" not in out
