"""Tests for NetBox reconciliation — TDD.

Written BEFORE the implementation. All pynetbox interactions are mocked.
Validates gating logic, journal entry creation, dedup, and error handling.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Gating: is_reconciliation_enabled
# ---------------------------------------------------------------------------
class TestReconciliationGating:
    """Reconciliation must be disabled by default and only enabled explicitly."""

    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Must be disabled when env var is not set."""
        monkeypatch.delenv("ENABLE_NETBOX_RECONCILIATION", raising=False)
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is False

    def test_enabled_with_lowercase_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Must enable with 'true'."""
        monkeypatch.setenv("ENABLE_NETBOX_RECONCILIATION", "true")
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is True

    def test_enabled_with_uppercase_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Must enable with 'True' (case-insensitive)."""
        monkeypatch.setenv("ENABLE_NETBOX_RECONCILIATION", "True")
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is True

    def test_enabled_with_all_caps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Must enable with 'TRUE'."""
        monkeypatch.setenv("ENABLE_NETBOX_RECONCILIATION", "TRUE")
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is True

    def test_disabled_with_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'yes' must NOT enable — only 'true' works."""
        monkeypatch.setenv("ENABLE_NETBOX_RECONCILIATION", "yes")
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is False

    def test_disabled_with_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'1' must NOT enable — only 'true' works."""
        monkeypatch.setenv("ENABLE_NETBOX_RECONCILIATION", "1")
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is False

    def test_disabled_with_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty string must NOT enable."""
        monkeypatch.setenv("ENABLE_NETBOX_RECONCILIATION", "")
        from agent.netbox_reconciler import is_reconciliation_enabled

        assert is_reconciliation_enabled() is False


# ---------------------------------------------------------------------------
# Journal entry creation
# ---------------------------------------------------------------------------
class TestCreateJournalEntry:
    """create_journal_entry must write structured drift data to NetBox."""

    def test_creates_entry_with_drift_details(self) -> None:
        """Journal entry must include device name, interface, expected, live."""
        from agent.netbox_reconciler import create_journal_entry

        api = MagicMock()
        device_mock = MagicMock()
        device_mock.id = 42
        api.dcim.devices.get.return_value = device_mock

        drifts = [
            {
                "device": "dc-spine-1",
                "interface": "Ethernet2",
                "field": "ipv4",
                "expected": "10.1.1.2/31",
                "live": "10.99.99.1/31",
            }
        ]
        result = create_journal_entry("dc-spine-1", drifts, api)
        assert result is True
        # Verify journal entry was created
        api.extras.journal_entries.create.assert_called_once()
        call_kwargs = api.extras.journal_entries.create.call_args
        entry_data = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]
        # Content must mention the drift details
        if isinstance(entry_data, dict):
            content = entry_data.get("comments", "")
            assert "Ethernet2" in content
            assert "10.1.1.2/31" in content

    def test_device_not_found_returns_false(self) -> None:
        """Must return False (no crash) when device doesn't exist in NetBox."""
        from agent.netbox_reconciler import create_journal_entry

        api = MagicMock()
        api.dcim.devices.get.return_value = None

        drifts = [
            {
                "device": "ghost",
                "interface": "Eth1",
                "field": "ipv4",
                "expected": "1.1.1.1/31",
                "live": "MISSING",
            }
        ]
        result = create_journal_entry("ghost", drifts, api)
        assert result is False
        api.extras.journal_entries.create.assert_not_called()

    def test_api_error_returns_false(self) -> None:
        """Must return False on any pynetbox error — never crash."""
        from agent.netbox_reconciler import create_journal_entry

        api = MagicMock()
        api.dcim.devices.get.side_effect = Exception("API timeout")

        result = create_journal_entry("dc-spine-1", [], api)
        assert result is False

    def test_no_device_modification(self) -> None:
        """Must NEVER call device.save() or modify device attributes."""
        from agent.netbox_reconciler import create_journal_entry

        api = MagicMock()
        device_mock = MagicMock()
        device_mock.id = 42
        api.dcim.devices.get.return_value = device_mock

        drifts = [
            {
                "device": "dc-spine-1",
                "interface": "Eth1",
                "field": "ipv4",
                "expected": "1.1.1.1/31",
                "live": "2.2.2.2/31",
            }
        ]
        create_journal_entry("dc-spine-1", drifts, api)
        device_mock.save.assert_not_called()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
class TestJournalDedup:
    """Must not create duplicate journal entries within 1 hour window."""

    def test_skips_duplicate_within_window(self) -> None:
        """If same drift signature exists in recent entries, skip."""
        from agent.netbox_reconciler import _is_duplicate_entry

        # Simulate a recent journal entry with matching content
        recent_entry = MagicMock()
        recent_entry.comments = "Ethernet2 | ipv4 | expected: 10.1.1.2/31"
        recent_entry.created = datetime.now(UTC).isoformat()

        assert _is_duplicate_entry("dc-spine-1", "Ethernet2", "ipv4", [recent_entry]) is True

    def test_allows_new_drift_signature(self) -> None:
        """Different interface = not a duplicate."""
        from agent.netbox_reconciler import _is_duplicate_entry

        recent_entry = MagicMock()
        recent_entry.comments = "Ethernet2 | ipv4 | expected: 10.1.1.2/31"
        recent_entry.created = datetime.now(UTC).isoformat()

        assert _is_duplicate_entry("dc-spine-1", "Ethernet3", "ipv4", [recent_entry]) is False

    def test_allows_after_window_expires(self) -> None:
        """Entries older than 1 hour should not block new entries."""
        from agent.netbox_reconciler import _is_duplicate_entry

        old_entry = MagicMock()
        old_entry.comments = "Ethernet2 | ipv4 | expected: 10.1.1.2/31"
        # 2 hours ago — outside the 1-hour window
        old_entry.created = "2020-01-01T00:00:00Z"

        assert _is_duplicate_entry("dc-spine-1", "Ethernet2", "ipv4", [old_entry]) is False


# ---------------------------------------------------------------------------
# reconcile_drifts (gating function)
# ---------------------------------------------------------------------------
class TestReconcileDrifts:
    """reconcile_drifts must gate on env var and handle errors gracefully."""

    @patch("agent.netbox_reconciler.is_reconciliation_enabled", return_value=False)
    def test_does_nothing_when_disabled(self, mock_enabled: MagicMock) -> None:
        """Must return False and do nothing when reconciliation is disabled."""
        from agent.netbox_reconciler import reconcile_drifts

        result = reconcile_drifts("dc-spine-1", [{"drift": "data"}])
        assert result is False

    @patch("agent.netbox_reconciler.is_reconciliation_enabled", return_value=True)
    @patch("agent.netbox_reconciler.create_journal_entry", return_value=True)
    @patch("agent.netbox_reconciler._get_netbox_api")
    def test_calls_create_when_enabled(
        self, mock_api: MagicMock, mock_create: MagicMock, mock_enabled: MagicMock
    ) -> None:
        """Must call create_journal_entry when enabled."""
        from agent.netbox_reconciler import reconcile_drifts

        drifts = [
            {
                "device": "dc-spine-1",
                "interface": "Eth1",
                "field": "ipv4",
                "expected": "x",
                "live": "y",
            }
        ]
        result = reconcile_drifts("dc-spine-1", drifts)
        assert result is True
        mock_create.assert_called_once()

    @patch("agent.netbox_reconciler.is_reconciliation_enabled", return_value=True)
    @patch("agent.netbox_reconciler._get_netbox_api", side_effect=Exception("NetBox down"))
    def test_api_failure_returns_false_no_crash(
        self, mock_api: MagicMock, mock_enabled: MagicMock
    ) -> None:
        """NetBox API failure must return False, never crash the agent."""
        from agent.netbox_reconciler import reconcile_drifts

        result = reconcile_drifts("dc-spine-1", [{"drift": "data"}])
        assert result is False


# ---------------------------------------------------------------------------
# FortiGate HA standby detection
# ---------------------------------------------------------------------------
class TestFortigateHaStandby:
    """HA standby detection must identify standby units from show output."""

    def test_detects_standby_from_ha_status(self) -> None:
        """Must identify a secondary/standby unit from HA status output."""
        from agent.skills.spec_compliance.skill import is_fortios_ha_standby

        ha_output = (
            "HA Health Status:\n"
            "Model: FortiGate-VM64-KVM\n"
            "Mode: HA A-P\n"
            "Group: 1\n"
            "Master:FGVMEVXYZ12345 , dc-fw-1\n"
            "Slave :FGVMEVXYZ67890 , dc-fw-2 *\n"
        )
        # dc-fw-2 has the * indicating it's the local unit, and it's listed as Slave
        assert is_fortios_ha_standby(ha_output) is True

    def test_detects_active_unit(self) -> None:
        """Active/master unit must return False."""
        from agent.skills.spec_compliance.skill import is_fortios_ha_standby

        ha_output = (
            "HA Health Status:\n"
            "Mode: HA A-P\n"
            "Master:FGVMEVXYZ12345 , dc-fw-1 *\n"
            "Slave :FGVMEVXYZ67890 , dc-fw-2\n"
        )
        assert is_fortios_ha_standby(ha_output) is False

    def test_standalone_returns_false(self) -> None:
        """Standalone unit (no HA or standalone primary) returns False."""
        from agent.skills.spec_compliance.skill import is_fortios_ha_standby

        ha_output = "HA Health Status:\nMode: HA A-P\nMaster:FGVMEVXYZ12345 , dc-fw-1 *\n"
        # Only one unit listed — it's the primary/standalone
        assert is_fortios_ha_standby(ha_output) is False

    def test_empty_output_returns_false(self) -> None:
        """Empty or unparseable output = assume active (safe default)."""
        from agent.skills.spec_compliance.skill import is_fortios_ha_standby

        assert is_fortios_ha_standby("") is False
        assert is_fortios_ha_standby("random garbage") is False
