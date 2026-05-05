"""Telegram notification module for the AI Infra Lab Agent.

Sends skill results, drift alerts, and status updates to a Telegram
chat via the Bot API. All credentials come from .env.

Rate-limiting: failed device alerts are sent at most once per cooldown
period (default 30 minutes) to avoid flooding the operator when a
device is permanently down across scheduled agent runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import requests

from scripts.credentials import load_credentials

# Rate-limit: suppress duplicate failure alerts within this window.
# Passes are always sent (state recovery is important to see immediately).
ALERT_COOLDOWN = timedelta(minutes=30)
_last_alert: dict[str, datetime] = {}  # key = "skill:device:fail"


def _is_rate_limited(skill_name: str, device_name: str) -> bool:
    """Return True if a failure alert for this skill+device was sent recently."""
    key = f"{skill_name}:{device_name}:fail"
    now = datetime.now(UTC)
    last = _last_alert.get(key)
    if last and (now - last) < ALERT_COOLDOWN:
        return True
    _last_alert[key] = now
    return False


def _get_bot_config() -> tuple[str, str]:
    """Load bot token and chat ID from credentials."""
    creds = load_credentials()
    return creds.telegram_bot_token, creds.telegram_chat_id


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a text message to the configured Telegram chat.

    Returns True on success, False on failure.
    """
    token, chat_id = _get_bot_config()
    if not token or not chat_id:
        return False

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        timeout=10,
    )
    return resp.status_code == 200


def send_skill_result(
    skill_name: str,
    device_name: str,
    passed: bool,
    details: str = "",
) -> bool:
    """Send a formatted skill result notification.

    Failure alerts are rate-limited per device+skill. Pass alerts always send
    (recovery from failure should be visible immediately).
    """
    if not passed and _is_rate_limited(skill_name, device_name):
        return False
    icon = "✅" if passed else "🔴"
    msg = f"{icon} <b>{skill_name}</b> — {device_name}\n"
    if details:
        msg += f"<pre>{details[:3000]}</pre>"
    return send_message(msg)


def send_drift_alert(
    device_name: str,
    drifts: list[dict],
) -> bool:
    """Send a drift alert with exact diff details.

    Rate-limited to one alert per device within the cooldown window.
    """
    if _is_rate_limited("spec_compliance", device_name):
        return False
    msg = f"⚠️ <b>DRIFT DETECTED</b> — {device_name}\n\n"
    for drift in drifts[:10]:
        iface = drift.get("interface", drift.get("field", ""))
        expected = drift.get("expected", "")
        live = drift.get("live", "")
        msg += f"• <b>{iface}</b>\n"
        msg += f"  Expected: <code>{expected}</code>\n"
        msg += f"  Live: <code>{live}</code>\n\n"
    if len(drifts) > 10:
        msg += f"... and {len(drifts) - 10} more drifts\n"
    return send_message(msg)


def send_summary(
    skill_name: str,
    total_devices: int,
    passed: int,
    failed: int,
    details: str = "",
) -> bool:
    """Send a skill run summary."""
    icon = "✅" if failed == 0 else "🔴"
    msg = (
        f"{icon} <b>{skill_name} Summary</b>\n"
        f"Devices: {total_devices} | Pass: {passed} | Fail: {failed}\n"
    )
    if details:
        msg += f"\n<pre>{details[:2000]}</pre>"
    return send_message(msg)
