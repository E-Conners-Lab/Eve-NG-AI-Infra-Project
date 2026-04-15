"""NetBox reconciliation — write drift journal entries back to NetBox.

When the agent detects spec compliance drift, this module optionally
creates a journal entry on the affected device in NetBox. This is a
write-back, NOT an auto-fix — the agent documents drift, it does not
correct it autonomously.

Gated by ENABLE_NETBOX_RECONCILIATION=true in .env.

Usage:
    from agent.netbox_reconciler import reconcile_drifts
    reconcile_drifts("dc-spine-1", drifts)
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Dedup window — skip journal entries if the same drift was logged recently
DEDUP_WINDOW = timedelta(hours=1)


def is_reconciliation_enabled() -> bool:
    """Check if NetBox reconciliation is enabled.

    Only case-insensitive ``"true"`` enables. Everything else
    (``"yes"``, ``"1"``, empty, unset) is disabled.
    """
    value = os.environ.get("ENABLE_NETBOX_RECONCILIATION", "")
    return value.lower() == "true"


def _get_netbox_api() -> object:
    """Create a pynetbox API instance from credentials."""
    import pynetbox

    from scripts.credentials import load_credentials

    creds = load_credentials()
    api = pynetbox.api(creds.netbox_url, token=creds.netbox_token)
    return api


def _is_duplicate_entry(
    device_name: str,
    interface: str,
    field: str,
    recent_entries: list,
) -> bool:
    """Check if a matching drift entry already exists within the dedup window.

    Matches on interface name and field name appearing in the journal
    entry comments. Entries older than DEDUP_WINDOW are ignored.
    """
    now = datetime.now(UTC)
    signature = f"{interface} | {field}"

    for entry in recent_entries:
        comments = getattr(entry, "comments", "")
        if signature not in comments:
            continue

        # Parse the entry timestamp
        created = getattr(entry, "created", "")
        if not created:
            continue

        try:
            if isinstance(created, str):
                # Handle ISO format with or without timezone
                entry_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
            else:
                entry_time = created

            if now - entry_time < DEDUP_WINDOW:
                return True
        except (ValueError, TypeError):
            continue

    return False


def _format_drift_comments(drifts: list[dict]) -> str:
    """Format drift details as markdown for the journal entry."""
    lines = [
        f"**Agent Drift Detection** — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    for drift in drifts:
        iface = drift.get("interface", drift.get("field", "unknown"))
        field = drift.get("field", "")
        expected = drift.get("expected", "")
        live = drift.get("live", "")
        lines.append(f"- {iface} | {field} | expected: {expected} | live: {live}")

    return "\n".join(lines)


def create_journal_entry(
    device_name: str,
    drifts: list[dict],
    api: object,
) -> bool:
    """Create a journal entry on the device in NetBox documenting drift.

    Returns True on success, False on any error (never crashes).
    """
    try:
        device = api.dcim.devices.get(name=device_name)
        if not device:
            logger.warning("Device %s not found in NetBox", device_name)
            return False

        # Check for duplicate entries (dedup within 1 hour)
        try:
            recent = list(
                api.extras.journal_entries.filter(
                    assigned_object_type="dcim.device",
                    assigned_object_id=device.id,
                )
            )
        except Exception:
            recent = []

        # Filter drifts that aren't duplicates
        new_drifts = []
        for drift in drifts:
            iface = drift.get("interface", drift.get("field", ""))
            field = drift.get("field", "")
            if not _is_duplicate_entry(device_name, iface, field, recent):
                new_drifts.append(drift)

        if not new_drifts:
            logger.info("All drifts for %s already logged (dedup)", device_name)
            return True

        comments = _format_drift_comments(new_drifts)
        api.extras.journal_entries.create(
            {
                "assigned_object_type": "dcim.device",
                "assigned_object_id": device.id,
                "kind": "warning",
                "comments": comments,
            }
        )
        logger.info("Created journal entry for %s (%d drifts)", device_name, len(new_drifts))
        return True

    except Exception as e:
        logger.warning("Failed to create journal entry for %s: %s", device_name, e)
        return False


def reconcile_drifts(device_name: str, drifts: list[dict]) -> bool:
    """Gate function — create a NetBox journal entry if reconciliation is enabled.

    Called from agent/runner.py after drift detection. Wrapped in
    try/except so a NetBox failure never blocks the agent.

    Returns True if journal entry was created, False otherwise.
    """
    if not is_reconciliation_enabled():
        return False

    try:
        api = _get_netbox_api()
        return create_journal_entry(device_name, drifts, api)
    except Exception as e:
        logger.warning("NetBox reconciliation failed for %s: %s", device_name, e)
        return False
