"""Secure credential management.

Loads credentials from environment variables or a .env file.
Never reads credentials from CLI arguments or stores them in shell history.

The .env file is read into a private dict — os.environ is NOT mutated.
Explicit environment variables always take precedence over .env values.

Usage:
    from scripts.credentials import load_credentials
    creds = load_credentials()
    print(creds.eve_ng_host)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    """Immutable container for all project credentials."""

    # EVE-NG API
    eve_ng_host: str = ""
    eve_ng_username: str = "admin"
    eve_ng_password: str = ""

    # NetBox API
    netbox_url: str = ""
    netbox_token: str = ""

    # Device SSH (pyATS / Scrapli)
    device_username: str = ""
    device_password: str = ""
    device_enable_password: str = ""

    # FortiGate (if different)
    fortigate_username: str = ""
    fortigate_password: str = ""

    # Lab owner contact (for NetBox contact assignment — no PII in code)
    lab_owner_name: str = ""
    lab_owner_email: str = ""

    # Telegram bot (agent notifications)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def _parse_dotenv(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict without touching os.environ.

    Reads KEY=VALUE pairs. Skips comments (#) and empty lines.
    Strips surrounding single/double quotes from values but preserves
    quotes that are part of the value interior.
    """
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matched surrounding quotes only (not interior quotes)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value

    return values


def _resolve(key: str, dotenv_values: dict[str, str], default: str = "") -> str:
    """Resolve a credential value: os.environ wins, then .env, then default."""
    return os.environ.get(key, dotenv_values.get(key, default))


def load_credentials(env_file: Path | None = None) -> Credentials:
    """Load credentials from environment variables and optionally a .env file.

    Environment variables always take precedence over .env values.
    The .env file is read into a private dict — os.environ is never mutated.

    Args:
        env_file: Path to .env file. Defaults to project root .env.

    Returns:
        Credentials dataclass with all values populated.
    """
    if env_file is None:
        env_file = Path(__file__).parent.parent / ".env"

    dotenv = _parse_dotenv(env_file)

    return Credentials(
        eve_ng_host=_resolve("EVE_NG_HOST", dotenv),
        eve_ng_username=_resolve("EVE_NG_USERNAME", dotenv, "admin"),
        eve_ng_password=_resolve("EVE_NG_PASSWORD", dotenv),
        netbox_url=_resolve("NETBOX_URL", dotenv),
        netbox_token=_resolve("NETBOX_TOKEN", dotenv),
        device_username=_resolve("DEVICE_USERNAME", dotenv),
        device_password=_resolve("DEVICE_PASSWORD", dotenv),
        device_enable_password=_resolve("DEVICE_ENABLE_PASSWORD", dotenv),
        fortigate_username=_resolve("FORTIGATE_USERNAME", dotenv),
        fortigate_password=_resolve("FORTIGATE_PASSWORD", dotenv),
        lab_owner_name=_resolve("LAB_OWNER_NAME", dotenv),
        lab_owner_email=_resolve("LAB_OWNER_EMAIL", dotenv),
        telegram_bot_token=_resolve("TELEGRAM_BOT_TOKEN", dotenv),
        telegram_chat_id=_resolve("TELEGRAM_CHAT_ID", dotenv),
    )


def require_credentials(*fields: str) -> Credentials:
    """Load credentials and verify required fields are set.

    Args:
        *fields: Names of Credentials fields that must be non-empty.

    Returns:
        Credentials dataclass.

    Raises:
        SystemExit: If any required field is empty.
    """
    creds = load_credentials()
    missing = [f for f in fields if not getattr(creds, f, "")]
    if missing:
        print(
            f"ERROR: Missing required credentials: {', '.join(missing)}\n"
            f"Set them in .env or as environment variables.\n"
            f"See .env.example for reference.",
            file=sys.stderr,
        )
        sys.exit(1)
    return creds
