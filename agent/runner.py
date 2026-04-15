"""Agent Runner — connects to managed devices, runs skills, reports results.

Uses Nornir for parallel device execution, Netmiko for SSH transport,
and the skill modules for validation logic. Results go to GAIT audit
logs and Telegram notifications.

Usage:
    python -m agent.runner                    # run all skills once
    python -m agent.runner --skill fabric_health
    python -m agent.runner --schedule 300     # run every 5 minutes
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from agent.skills.fabric_health.skill import get_managed_devices, parse_bgp_summary
from agent.skills.spec_compliance.skill import compare_asn, compare_interfaces
from agent.telegram_notifier import send_drift_alert, send_skill_result, send_summary
from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
GAIT_LOG_DIR = Path(__file__).parent.parent / "logs"

# Netmiko device_type per platform
NETMIKO_DEVICE_TYPE: dict[str, str] = {
    "arista_eos": "arista_eos",
    "cisco_iosxe": "cisco_xe",
    "fortinet_fortios": "fortinet",
}


def _gait_log(log_file: Path, entry: dict) -> None:
    """Append a GAIT audit entry."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _find_device_in_spec(spec: dict, device_name: str) -> tuple[dict, str]:
    """Find a device dict and its platform from the spec."""
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name:
                return dev, dev["platform"]
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] == device_name:
            return dev, dev["platform"]
    for _zk, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            if dev["name"] == device_name:
                return dev, dev["platform"]
    return {}, ""


def _connect(
    device_name: str, platform: str, mgmt_ip: str, username: str, password: str
) -> ConnectHandler | None:
    """Create a Netmiko connection to a device."""
    device_type = NETMIKO_DEVICE_TYPE.get(platform)
    if not device_type:
        return None
    try:
        return ConnectHandler(
            device_type=device_type,
            host=mgmt_ip,
            username=username,
            password=password,
            timeout=15,
        )
    except (NetmikoAuthenticationException, NetmikoTimeoutException) as e:
        print(f"  {device_name}: connection failed — {e}")
        return None


def run_fabric_health(spec: dict, creds: object, log_file: Path) -> dict:
    """Run the fabric_health skill on all managed fabric devices."""
    managed = get_managed_devices(spec)
    mgmt_ips = get_mgmt_ips()
    results: dict = {"passed": 0, "failed": 0, "devices": {}}

    fabric_devices = [
        name
        for name in managed
        if any(
            name in [d["name"] for d in spec["sites"].get(sk, {}).get("devices", [])]
            for sk in ("dc_east", "dr_west")
        )
    ]

    print(f"\n  fabric_health: checking {len(fabric_devices)} fabric devices")

    for name in fabric_devices:
        dev, platform = _find_device_in_spec(spec, name)
        if not dev or platform not in ("arista_eos",):
            continue

        mgmt_ip = mgmt_ips.get(name, "")
        if not mgmt_ip:
            continue

        conn = _connect(name, platform, mgmt_ip, creds.device_username, creds.device_password)
        if not conn:
            results["failed"] += 1
            results["devices"][name] = {"status": "unreachable"}
            continue

        try:
            output = conn.send_command("show ip bgp summary")
            bgp_result = parse_bgp_summary(output, platform)
            conn.disconnect()

            _gait_log(
                log_file,
                {
                    "action": "skill_check",
                    "skill": "fabric_health",
                    "device": name,
                    "result": bgp_result,
                },
            )

            if bgp_result["healthy"]:
                results["passed"] += 1
                send_skill_result(
                    "fabric_health",
                    name,
                    passed=True,
                    details=f"{bgp_result['established']} sessions up",
                )
            else:
                results["failed"] += 1
                down_list = ", ".join(n["neighbor"] for n in bgp_result["down_neighbors"])
                send_skill_result("fabric_health", name, passed=False, details=f"DOWN: {down_list}")

            results["devices"][name] = bgp_result
        except Exception as e:
            results["failed"] += 1
            results["devices"][name] = {"status": f"error: {e}"}
            with contextlib.suppress(Exception):
                conn.disconnect()

    return results


def run_branch_connectivity(spec: dict, creds: object, log_file: Path) -> dict:
    """Run the branch_connectivity skill on branch CE routers."""
    mgmt_ips = get_mgmt_ips()
    results: dict = {"passed": 0, "failed": 0, "devices": {}}

    branch_devices = ["br-ce-1", "dc-ce-1", "dr-ce-1"]
    print(f"\n  branch_connectivity: checking {len(branch_devices)} CE routers")

    for name in branch_devices:
        dev, platform = _find_device_in_spec(spec, name)
        mgmt_ip = mgmt_ips.get(name, "")
        if not mgmt_ip or not dev:
            continue

        conn = _connect(name, platform, mgmt_ip, creds.device_username, creds.device_password)
        if not conn:
            results["failed"] += 1
            results["devices"][name] = {"status": "unreachable"}
            continue

        try:
            output = conn.send_command("show ip bgp summary")
            bgp_result = parse_bgp_summary(output, platform)
            conn.disconnect()

            _gait_log(
                log_file,
                {
                    "action": "skill_check",
                    "skill": "branch_connectivity",
                    "device": name,
                    "result": bgp_result,
                },
            )

            if bgp_result["healthy"]:
                results["passed"] += 1
                send_skill_result(
                    "branch_connectivity",
                    name,
                    passed=True,
                    details=f"{bgp_result['established']} PE sessions up",
                )
            else:
                results["failed"] += 1
                down_list = ", ".join(n["neighbor"] for n in bgp_result["down_neighbors"])
                send_skill_result(
                    "branch_connectivity", name, passed=False, details=f"DOWN: {down_list}"
                )

            results["devices"][name] = bgp_result
        except Exception as e:
            results["failed"] += 1
            results["devices"][name] = {"status": f"error: {e}"}
            with contextlib.suppress(Exception):
                conn.disconnect()

    return results


def run_spec_compliance(spec: dict, creds: object, log_file: Path) -> dict:
    """Run the spec_compliance skill on all managed devices."""
    managed = get_managed_devices(spec)
    mgmt_ips = get_mgmt_ips()
    results: dict = {"passed": 0, "failed": 0, "devices": {}, "all_drifts": []}

    print(f"\n  spec_compliance: checking {len(managed)} managed devices")

    for name in managed:
        dev, platform = _find_device_in_spec(spec, name)
        mgmt_ip = mgmt_ips.get(name, "")
        if not dev or not mgmt_ip:
            continue

        device_type = NETMIKO_DEVICE_TYPE.get(platform)
        if not device_type:
            continue

        username = creds.device_username
        password = creds.device_password
        if platform == "fortinet_fortios":
            username = creds.fortigate_username or username
            password = creds.fortigate_password or password

        conn = _connect(name, platform, mgmt_ip, username, password)
        if not conn:
            results["failed"] += 1
            results["devices"][name] = {"status": "unreachable"}
            continue

        try:
            drifts: list[dict] = []

            # Check interface IPs
            if platform == "arista_eos" or platform == "cisco_iosxe":
                output = conn.send_command("show ip interface brief")
            elif platform == "fortinet_fortios":
                output = conn.send_command("get system interface physical")
            else:
                output = ""

            # Parse live interface IPs (simplified — extract IP per interface)
            live_interfaces: dict[str, str] = {}
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    iface_name = parts[0]
                    # Look for IP-like pattern in the line
                    for part in parts[1:]:
                        if "/" in part and "." in part:
                            live_interfaces[iface_name] = part
                            break

            iface_drifts = compare_interfaces(name, dev, live_interfaces)
            drifts.extend(iface_drifts)

            # Check ASN
            if "asn" in dev and platform in ("arista_eos", "cisco_iosxe"):
                bgp_output = conn.send_command("show ip bgp summary")
                # Extract local AS from output
                for bgp_line in bgp_output.splitlines():
                    if "local AS" in bgp_line.lower() or "local as" in bgp_line.lower():
                        for word in bgp_line.split():
                            try:
                                live_asn = int(word)
                                if live_asn > 0:
                                    asn_drift = compare_asn(name, dev["asn"], live_asn)
                                    if asn_drift:
                                        drifts.append(asn_drift)
                                    break
                            except ValueError:
                                continue
                        break

            conn.disconnect()

            _gait_log(
                log_file,
                {
                    "action": "skill_check",
                    "skill": "spec_compliance",
                    "device": name,
                    "drifts": len(drifts),
                },
            )

            if drifts:
                results["failed"] += 1
                results["all_drifts"].extend(drifts)
                send_drift_alert(name, drifts)
            else:
                results["passed"] += 1
                send_skill_result("spec_compliance", name, passed=True, details="No drift detected")

            results["devices"][name] = {"drifts": drifts}
        except Exception as e:
            results["failed"] += 1
            results["devices"][name] = {"status": f"error: {e}"}
            with contextlib.suppress(Exception):
                conn.disconnect()

    return results


def run_all_skills(spec: dict, skill_filter: str = "") -> None:
    """Run all (or filtered) skills and report results."""
    creds = require_credentials("device_username", "device_password")
    log_file = GAIT_LOG_DIR / f"agent_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.jsonl"

    _gait_log(log_file, {"action": "agent_run_start", "skill_filter": skill_filter})
    print(f"Agent run started — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    skills_to_run = {
        "fabric_health": run_fabric_health,
        "branch_connectivity": run_branch_connectivity,
        "spec_compliance": run_spec_compliance,
    }

    if skill_filter:
        skills_to_run = {k: v for k, v in skills_to_run.items() if k == skill_filter}

    total_passed = 0
    total_failed = 0

    for skill_name, skill_fn in skills_to_run.items():
        print(f"\nRunning: {skill_name}")
        result = skill_fn(spec, creds, log_file)
        total_passed += result["passed"]
        total_failed += result["failed"]

        send_summary(
            skill_name,
            result["passed"] + result["failed"],
            result["passed"],
            result["failed"],
        )

    _gait_log(
        log_file,
        {
            "action": "agent_run_end",
            "total_passed": total_passed,
            "total_failed": total_failed,
        },
    )
    print(f"\nAgent run complete: {total_passed} passed, {total_failed} failed")
    print(f"GAIT log: {log_file}")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="AI Infra Lab Agent")
    parser.add_argument("--skill", default="", help="Run a specific skill")
    parser.add_argument(
        "--schedule", type=int, default=0, help="Run on schedule (seconds between runs, 0=once)"
    )
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())

    if args.schedule > 0:
        print(f"Agent scheduled: running every {args.schedule} seconds")
        while True:
            run_all_skills(spec, args.skill)
            print(f"\nSleeping {args.schedule}s until next run...")
            time.sleep(args.schedule)
    else:
        run_all_skills(spec, args.skill)


if __name__ == "__main__":
    main()
