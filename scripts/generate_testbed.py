"""Generate pyATS testbed from YAML spec and bootstrap config.

Produces agent/testbed.yaml with managed + observed devices,
management IPs, and platform-to-Scrapli driver mappings.
Credentials reference environment variables — never hardcoded.

Usage:
    python -m scripts.generate_testbed
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from scripts.bootstrap_config import get_mgmt_ips

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
OUTPUT_PATH = Path(__file__).parent.parent / "agent" / "testbed.yaml"

# Scrapli platform mapping for pyATS-compatible testbed
PLATFORM_TO_OS: dict[str, str] = {
    "arista_eos": "eos",
    "cisco_iosxe": "iosxe",
    "fortinet_fortios": "fortinet_fortios",
    "linux": "linux",
}

# Scrapli transport driver per platform
PLATFORM_TO_DRIVER: dict[str, str] = {
    "arista_eos": "arista_eos",
    "cisco_iosxe": "cisco_iosxe",
    "fortinet_fortios": "fortinet_fortios",
    "linux": "linux",
}


def _collect_devices(spec: dict) -> list[dict]:
    """Collect all network devices (not hosts) from the spec."""
    devices = []
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["role"] != "host":
                devices.append(dev)
    for dev in spec.get("wan_transport", {}).get("devices", []):
        devices.append(dev)
    for _zk, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            devices.append(dev)
    return devices


def generate_testbed(spec: dict) -> dict:
    """Build a pyATS testbed dict from the spec."""
    mgmt_ips = get_mgmt_ips()
    devices = _collect_devices(spec)

    # Agent boundary for filtering
    agent = spec.get("agent", {}).get("boundary", {})
    managed = set(agent.get("managed", []))
    excluded = set(agent.get("excluded", []))

    testbed: dict = {
        "testbed": {
            "name": "ai-infra-lab",
            "credentials": {
                "default": {
                    "username": "%ENV{DEVICE_USERNAME}",
                    "password": "%ENV{DEVICE_PASSWORD}",
                },
                "enable": {
                    "password": "%ENV{DEVICE_ENABLE_PASSWORD}",
                },
            },
        },
        "devices": {},
    }

    for dev in devices:
        name = dev["name"]
        platform = dev["platform"]
        os_type = PLATFORM_TO_OS.get(platform, "linux")
        mgmt_ip = mgmt_ips.get(name)

        if not mgmt_ip:
            continue

        # Determine device role in testbed
        if name in managed:
            role = "managed"
        elif name in excluded:
            role = "excluded"
        else:
            role = "observed"

        device_entry: dict = {
            "os": os_type,
            "custom": {
                "agent_role": role,
                "platform": platform,
                "spec_role": dev["role"],
            },
            "connections": {
                "cli": {
                    "protocol": "ssh",
                    "ip": mgmt_ip,
                    "port": 22,
                },
            },
        }

        # FortiGate uses different credentials
        if platform == "fortinet_fortios":
            device_entry["credentials"] = {
                "default": {
                    "username": "%ENV{FORTIGATE_USERNAME}",
                    "password": "%ENV{FORTIGATE_PASSWORD}",
                },
            }

        testbed["devices"][name] = device_entry

    return testbed


def main() -> None:
    """CLI entrypoint."""
    if not SPEC_PATH.exists():
        print(f"ERROR: Spec not found: {SPEC_PATH}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(SPEC_PATH.read_text())
    testbed = generate_testbed(spec)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        yaml.dump(testbed, f, default_flow_style=False, sort_keys=False)

    device_count = len(testbed["devices"])
    managed_count = sum(
        1 for d in testbed["devices"].values() if d["custom"]["agent_role"] == "managed"
    )
    print(f"Generated testbed with {device_count} devices ({managed_count} managed)")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
