"""Telegram notification module for the AI Infra Lab Agent.

Sends skill results, drift alerts, and status updates to a Telegram
chat via the Bot API. All credentials come from .env.
"""

from __future__ import annotations

import requests

from scripts.credentials import load_credentials


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
    """Send a formatted skill result notification."""
    icon = "✅" if passed else "🔴"
    msg = f"{icon} <b>{skill_name}</b> — {device_name}\n"
    if details:
        msg += f"<pre>{details[:3000]}</pre>"
    return send_message(msg)


def send_drift_alert(
    device_name: str,
    drifts: list[dict],
) -> bool:
    """Send a drift alert with exact diff details."""
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
