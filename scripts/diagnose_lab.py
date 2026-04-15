"""Diagnostic script — check live device state via SSH.

Connects to key devices across all roles (spine, leaf, border, CE)
and collects show commands to determine actual running state. Reports
whether BGP config is present, interfaces are up, and sessions are
established.

Usage:
    python -m scripts.diagnose_lab
    python -m scripts.diagnose_lab --device dc-spine-1
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

DIAG_DIR = Path(__file__).parent.parent / "logs" / "diagnostics"

# Devices to check by default — one per role across both fabrics + WAN
DEFAULT_DEVICES: dict[str, str] = {
    "dc-spine-1": "arista_eos",
    "dc-leaf-1": "arista_eos",
    "dc-border-1": "arista_eos",
    "dr-leaf-1": "arista_eos",
    "dc-ce-1": "cisco_xe",
    "br-ce-1": "cisco_xe",
    "sp-pe-1": "cisco_xe",
}

# Show commands per platform
COMMANDS: dict[str, list[str]] = {
    "arista_eos": [
        "show running-config section router bgp",
        "show ip bgp summary",
        "show ip interface brief",
        "show interfaces status",
        "show ip route summary",
    ],
    "cisco_xe": [
        "show running-config | section router bgp",
        "show ip bgp summary",
        "show ip interface brief",
        "show ip route summary",
    ],
    "fortinet": [
        "get system status",
        "get system interface physical",
        "get router info bgp summary",
    ],
}


def diagnose_device(
    device_name: str,
    device_type: str,
    mgmt_ip: str,
    username: str,
    password: str,
) -> dict:
    """SSH into a device and collect diagnostic show commands."""
    result: dict = {
        "device": device_name,
        "device_type": device_type,
        "mgmt_ip": mgmt_ip,
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "unknown",
        "commands": {},
        "analysis": {},
    }

    try:
        conn = ConnectHandler(
            device_type=device_type,
            host=mgmt_ip,
            username=username,
            password=password,
            secret=password,
            timeout=15,
        )
        conn.enable()
        result["status"] = "connected"

        for cmd in COMMANDS.get(device_type, []):
            output = conn.send_command(cmd)
            result["commands"][cmd] = output

        conn.disconnect()

        # Analyze the collected data
        result["analysis"] = _analyze(device_name, device_type, result["commands"])

    except (NetmikoAuthenticationException, NetmikoTimeoutException) as e:
        result["status"] = f"connection_failed: {e}"
    except Exception as e:
        result["status"] = f"error: {e}"

    return result


def _analyze(device_name: str, device_type: str, commands: dict) -> dict:
    """Analyze collected show command output for key indicators."""
    analysis: dict = {}

    # Check BGP config presence
    if device_type == "arista_eos":
        bgp_config = commands.get("show running-config section router bgp", "")
        analysis["bgp_config_present"] = "router bgp" in bgp_config
        analysis["bgp_config_lines"] = len(bgp_config.splitlines()) if bgp_config else 0

        # Check if address-family ipv4 has activated neighbors
        analysis["af_ipv4_present"] = "address-family ipv4" in bgp_config
        analysis["af_evpn_present"] = "address-family evpn" in bgp_config

        # Count configured neighbors
        neighbor_count = sum(1 for line in bgp_config.splitlines() if "remote-as" in line)
        analysis["configured_neighbors"] = neighbor_count

    elif device_type == "cisco_xe":
        bgp_config = commands.get("show running-config | section router bgp", "")
        analysis["bgp_config_present"] = "router bgp" in bgp_config
        analysis["bgp_config_lines"] = len(bgp_config.splitlines()) if bgp_config else 0

    # Check BGP summary for established sessions
    # Handles both IOS-XE format (IP first) and EOS 4.33+ (Description first)
    import re

    bgp_summary = commands.get("show ip bgp summary", "")
    ip_pattern = re.compile(r"\d+\.\d+\.\d+\.\d+")
    established = 0
    total_peers = 0
    for line in bgp_summary.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Skip header/info lines
        if any(
            kw in line_stripped.lower()
            for kw in (
                "identifier",
                "router",
                "vrf",
                "status",
                "neighbor",
                "network",
                "path",
                "memory",
                "scan",
                "peaked",
                "activity",
                "filter",
                "cache",
                "using",
                "table",
            )
        ):
            continue
        # Find first IP in the line
        ip_match = ip_pattern.search(line_stripped)
        if not ip_match:
            continue
        remainder = line_stripped[ip_match.start() :]
        parts = remainder.split()
        if len(parts) < 9:
            continue
        total_peers += 1
        # If last field is a number, session is established
        try:
            int(parts[-1])
            established += 1
        except ValueError:
            pass  # State string like Active/Idle

    analysis["bgp_total_peers"] = total_peers
    analysis["bgp_established"] = established
    analysis["bgp_down"] = total_peers - established

    # Check interface status
    intf_output = commands.get("show ip interface brief", "")
    up_intfs = 0
    down_intfs = 0
    for line in intf_output.splitlines():
        if "up" in line.lower() and not line.strip().startswith("Interface"):
            up_intfs += 1
        elif "down" in line.lower() or "administratively" in line.lower():
            down_intfs += 1
    analysis["interfaces_up"] = up_intfs
    analysis["interfaces_down"] = down_intfs

    return analysis


def print_report(results: list[dict]) -> None:
    """Print a human-readable diagnostic report."""
    print("\n" + "=" * 72)
    print("  LAB DIAGNOSTIC REPORT")
    print(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 72)

    for r in results:
        name = r["device"]
        status = r["status"]
        a = r.get("analysis", {})

        print(f"\n{'─' * 72}")
        print(f"  {name} ({r['device_type']}) — {r['mgmt_ip']}")
        print(f"{'─' * 72}")

        if "connection_failed" in status or "error" in status:
            print(f"  STATUS: UNREACHABLE — {status}")
            continue

        print(f"  STATUS: {status}")

        # BGP config
        bgp_present = a.get("bgp_config_present", False)
        bgp_lines = a.get("bgp_config_lines", 0)
        print(f"  BGP config present: {'YES' if bgp_present else 'NO'} ({bgp_lines} lines)")

        if r["device_type"] == "arista_eos":
            print(f"  address-family ipv4: {'YES' if a.get('af_ipv4_present') else 'NO'}")
            print(f"  address-family evpn: {'YES' if a.get('af_evpn_present') else 'NO'}")
            print(f"  Configured neighbors: {a.get('configured_neighbors', 0)}")

        # BGP sessions
        total = a.get("bgp_total_peers", 0)
        estab = a.get("bgp_established", 0)
        down = a.get("bgp_down", 0)
        if total > 0:
            print(f"  BGP sessions: {estab}/{total} established, {down} down")
        else:
            print("  BGP sessions: NONE (no peers in BGP summary)")

        # Interfaces
        up = a.get("interfaces_up", 0)
        dn = a.get("interfaces_down", 0)
        print(f"  Interfaces: {up} up, {dn} down")

        # Print raw BGP config for Arista devices (key diagnostic)
        if r["device_type"] == "arista_eos":
            bgp_raw = r["commands"].get("show running-config section router bgp", "")
            if bgp_raw:
                print("\n  --- show running-config section router bgp ---")
                for line in bgp_raw.splitlines()[:40]:
                    print(f"  | {line}")
                if len(bgp_raw.splitlines()) > 40:
                    print(f"  | ... ({len(bgp_raw.splitlines()) - 40} more lines)")

        # Print BGP summary for all devices
        bgp_sum = r["commands"].get("show ip bgp summary", "")
        if bgp_sum:
            print("\n  --- show ip bgp summary ---")
            for line in bgp_sum.splitlines()[:20]:
                print(f"  | {line}")

    # Summary
    print(f"\n{'=' * 72}")
    print("  SUMMARY")
    print(f"{'=' * 72}")
    reachable = sum(1 for r in results if r["status"] == "connected")
    unreachable = len(results) - reachable
    total_estab = sum(r.get("analysis", {}).get("bgp_established", 0) for r in results)
    total_down = sum(r.get("analysis", {}).get("bgp_down", 0) for r in results)
    total_no_config = sum(
        1
        for r in results
        if r["status"] == "connected" and not r.get("analysis", {}).get("bgp_config_present", False)
    )

    print(f"  Devices reachable: {reachable}/{len(results)}")
    print(f"  Devices unreachable: {unreachable}")
    print(f"  BGP sessions established (total): {total_estab}")
    print(f"  BGP sessions down (total): {total_down}")
    print(f"  Devices with NO BGP config: {total_no_config}")

    if total_no_config > 0:
        no_bgp = [
            r["device"]
            for r in results
            if r["status"] == "connected"
            and not r.get("analysis", {}).get("bgp_config_present", False)
        ]
        print(f"  Missing BGP config on: {', '.join(no_bgp)}")
        print("  DIAGNOSIS: Config was likely lost after reboot (no write memory)")

    arista_zero = [
        r["device"]
        for r in results
        if r.get("analysis", {}).get("bgp_config_present", False)
        and r.get("analysis", {}).get("bgp_total_peers", 0) == 0
        and r["device_type"] == "arista_eos"
    ]
    if arista_zero:
        print(f"  Arista devices with config but 0 peers: {', '.join(arista_zero)}")
        print("  DIAGNOSIS: Config context bleed — address-family not exited properly")

    print(f"{'=' * 72}\n")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Diagnose live lab device state")
    parser.add_argument("--device", default="", help="Check a single device")
    args = parser.parse_args()

    creds = require_credentials("device_username", "device_password")
    mgmt_ips = get_mgmt_ips()

    if args.device:
        devices = {args.device: DEFAULT_DEVICES.get(args.device, "arista_eos")}
    else:
        devices = DEFAULT_DEVICES

    results: list[dict] = []
    for name, dtype in devices.items():
        ip = mgmt_ips.get(name, "")
        if not ip:
            print(f"  {name}: SKIP — no management IP in bootstrap")
            continue

        print(f"  Checking {name} ({ip})...")
        result = diagnose_device(name, dtype, ip, creds.device_username, creds.device_password)
        results.append(result)

    # Save raw results to JSON
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_path = DIAG_DIR / f"diag_{ts}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nRaw results saved: {out_path}")

    # Print human-readable report
    print_report(results)


if __name__ == "__main__":
    main()
