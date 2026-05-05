"""Shared building blocks for troubleshooting scenarios.

A `Scenario` is a dataclass bundling metadata + three behaviour hooks:
  - `inject(conn)`  applies the fault to a live device
  - `detect(conn)`  returns (fault_present, evidence) for grading
  - `fix(conn)`     applies the targeted repair (the answer)

Scenarios are registered into a process-global `REGISTRY` so the CLI and
tests share one source of truth. Connectivity reuses the existing project
plumbing (`scripts.bootstrap_config`, `scripts.credentials`) so we don't
maintain two inventory paths.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from netmiko import ConnectHandler

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import load_credentials

CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "generated"
RUNBOOKS_DIR = Path(__file__).parent / "runbooks"

VALID_DIFFICULTIES = ("beginner", "intermediate", "advanced")

NETMIKO_DEVICE_TYPE: dict[str, str] = {
    "arista_eos": "arista_eos",
    "cisco_iosxe": "cisco_xe",
    "fortinet_fortios": "fortinet",
}

DetectResult = tuple[bool, str]


@dataclass(frozen=True)
class Scenario:
    """One curated troubleshooting fault.

    Hooks receive a netmiko-like connection object so tests can swap in mocks.
    """

    name: str
    device: str
    platform: str
    difficulty: str
    summary: str
    symptoms: str
    runbook: str  # path relative to project root, e.g. "troubleshooting/runbooks/foo.md"
    inject: Callable[[object], None]
    detect: Callable[[object], DetectResult]
    fix: Callable[[object], None]

    def __post_init__(self) -> None:
        if self.difficulty not in VALID_DIFFICULTIES:
            raise ValueError(
                f"unknown difficulty {self.difficulty!r}, expected one of {VALID_DIFFICULTIES}"
            )
        if self.platform not in NETMIKO_DEVICE_TYPE:
            raise ValueError(
                f"unknown platform {self.platform!r}, expected one of {list(NETMIKO_DEVICE_TYPE)}"
            )


class Registry:
    """Insertion-ordered registry of scenarios keyed by name."""

    def __init__(self) -> None:
        self._items: dict[str, Scenario] = {}

    def register(self, scenario: Scenario) -> None:
        if scenario.name in self._items:
            raise ValueError(f"scenario {scenario.name!r} already registered")
        self._items[scenario.name] = scenario

    def get(self, name: str) -> Scenario:
        if name not in self._items:
            raise KeyError(f"unknown scenario: {name}")
        return self._items[name]

    def all(self) -> list[Scenario]:
        return list(self._items.values())


REGISTRY = Registry()


def connect_device(device: str, platform: str, timeout: int = 30) -> ConnectHandler:
    """Open an SSH session to a managed device using project credentials."""
    mgmt_ip = get_mgmt_ips().get(device)
    if not mgmt_ip:
        raise KeyError(f"no management IP for device {device!r}")

    creds = load_credentials()
    if platform == "fortinet_fortios":
        username = creds.fortigate_username or creds.device_username
        password = creds.fortigate_password or creds.device_password
    else:
        username = creds.device_username
        password = creds.device_password

    device_type = NETMIKO_DEVICE_TYPE.get(platform)
    if not device_type:
        raise ValueError(f"unsupported platform {platform!r}")

    return ConnectHandler(
        device_type=device_type,
        host=mgmt_ip,
        username=username,
        password=password,
        secret=password,
        timeout=timeout,
    )


def restore_clean_config(device: str, platform: str) -> bool:
    """Push the spec-rendered config from configs/generated/ to fully reset.

    The "nuclear option" — equivalent to chaos_test rollback. Always restores
    a known-good state regardless of how badly the user mangled the device.
    """
    from scripts.push_configs import push_config_to_device

    mgmt_ip = get_mgmt_ips().get(device)
    if not mgmt_ip:
        raise KeyError(f"no management IP for device {device!r}")

    config_path = CONFIGS_DIR / f"{device}.cfg"
    if not config_path.exists():
        raise FileNotFoundError(f"no clean config for {device}: {config_path}")

    creds = load_credentials()
    if platform == "fortinet_fortios":
        username = creds.fortigate_username or creds.device_username
        password = creds.fortigate_password or creds.device_password
    else:
        username = creds.device_username
        password = creds.device_password

    log_file = Path("logs") / "troubleshooting_restore.jsonl"
    return push_config_to_device(
        device, platform, config_path, mgmt_ip, username, password, log_file
    )
