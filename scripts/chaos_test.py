"""Chaos testing — inject faults into live devices, verify agent detection.

Targets all 15 managed network devices (Arista, Cisco, FortiGate).
Fault types are platform-aware. Rollback is guaranteed via try/finally.

Usage:
    python -m scripts.chaos_test
    python -m scripts.chaos_test --dry-run
    python -m scripts.chaos_test --predict    # include Batfish impact prediction
    python -m scripts.chaos_test --device dc-spine-1
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent.skills.fabric_health.skill import get_managed_devices
from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

logger = logging.getLogger(__name__)

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "generated"

# Platforms that can't be chaos targets (no SSH config push)
EXCLUDED_PLATFORMS = {"linux"}


@dataclass
class Fault:
    """Describes an injected fault and what the agent should detect."""

    fault_type: str
    device: str
    platform: str
    detail: str  # interface name, neighbor IP, etc.
    expected_skill: str  # which agent skill should catch this


# ---------------------------------------------------------------------------
# Fault type definitions per platform
# ---------------------------------------------------------------------------

# Each fault type: {name, description, expected_skill, platforms, roles}
FAULT_CATALOG: list[dict] = [
    {
        "name": "shut_interface",
        "description": "Shutdown a data-plane interface",
        "expected_skill": "fabric_health",
        "platforms": {"arista_eos", "cisco_iosxe", "fortinet_fortios"},
        "roles": None,  # all roles
    },
    {
        "name": "wrong_asn",
        "description": "Change a BGP neighbor's remote-as to a wrong value",
        "expected_skill": "fabric_health",
        "platforms": {"arista_eos", "cisco_iosxe"},
        "roles": None,
    },
    {
        "name": "remove_vni",
        "description": "Remove a VNI-to-VLAN mapping",
        "expected_skill": "spec_compliance",
        "platforms": {"arista_eos"},
        "roles": {"leaf"},
    },
    {
        "name": "change_ip",
        "description": "Change an interface IP to a wrong value",
        "expected_skill": "spec_compliance",
        "platforms": {"arista_eos", "cisco_iosxe", "fortinet_fortios"},
        "roles": None,
    },
]


def get_eligible_devices(spec: dict) -> list[dict]:
    """Return all managed network devices eligible for chaos testing.

    Excludes hosts (no SSH/config) and SP PEs (excluded from agent boundary).
    """
    managed_names = set(get_managed_devices(spec))
    eligible: list[dict] = []

    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if (
                dev["name"] in managed_names
                and dev.get("platform") not in EXCLUDED_PLATFORMS
                and dev.get("role") != "host"
            ):
                eligible.append(dev)

    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] in managed_names:
            eligible.append(dev)

    for _zk, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            if dev["name"] in managed_names:
                eligible.append(dev)

    return eligible


def get_fault_types_for_platform(platform: str, role: str) -> list[dict]:
    """Return fault types available for a given platform and role."""
    result: list[dict] = []
    for fault in FAULT_CATALOG:
        if platform not in fault["platforms"]:
            continue
        if fault["roles"] is not None and role not in fault["roles"]:
            continue
        result.append(fault)
    return result


def build_fault_commands(platform: str, fault_type: str, **kwargs: str | int) -> list[str]:
    """Build CLI commands to inject a fault on a specific platform.

    Returns a list of commands to send via Netmiko/write_channel.
    """
    if fault_type == "shut_interface":
        iface = kwargs["interface"]
        if platform == "arista_eos":
            return [f"interface {iface}", "shutdown"]
        if platform == "cisco_iosxe":
            return [f"interface {iface}", "shutdown"]
        if platform == "fortinet_fortios":
            return [
                "config system interface",
                f'edit "{iface}"',
                "set status down",
                "next",
                "end",
            ]

    if fault_type == "wrong_asn":
        neighbor = kwargs["neighbor"]
        wrong_asn = kwargs["wrong_asn"]
        if platform == "arista_eos":
            return [
                f"router bgp {kwargs.get('local_asn', '')}".strip(),
                f"neighbor {neighbor} remote-as {wrong_asn}",
            ]
        if platform == "cisco_iosxe":
            return [
                f"router bgp {kwargs.get('local_asn', '')}".strip(),
                f"neighbor {neighbor} remote-as {wrong_asn}",
            ]

    if fault_type == "remove_vni":
        vni = kwargs.get("vni", "")
        if platform == "arista_eos":
            return ["interface Vxlan1", f"no vxlan vni {vni}"]

    if fault_type == "change_ip":
        iface = kwargs["interface"]
        wrong_ip = kwargs["wrong_ip"]
        if platform == "arista_eos":
            return [f"interface {iface}", f"ip address {wrong_ip}"]
        if platform == "cisco_iosxe":
            # IOS-XE needs dotted mask
            ip_part = wrong_ip.split("/")[0]
            return [f"interface {iface}", f"ip address {ip_part} 255.255.255.254"]
        if platform == "fortinet_fortios":
            ip_part = wrong_ip.split("/")[0]
            return [
                "config system interface",
                f'edit "{iface}"',
                f"set ip {ip_part} 255.255.255.254",
                "next",
                "end",
            ]

    return []


def select_random_fault(spec: dict, device_filter: str = "") -> Fault:
    """Select a random device and fault type from the eligible pool.

    If device_filter is set, target that specific device.
    """
    eligible = get_eligible_devices(spec)
    if not eligible:
        raise ValueError("No eligible devices for chaos testing")

    if device_filter:
        eligible = [d for d in eligible if d["name"] == device_filter]
        if not eligible:
            raise ValueError(f"Device '{device_filter}' not found in eligible pool")

    device = random.choice(eligible)
    platform = device["platform"]
    role = device["role"]
    fault_types = get_fault_types_for_platform(platform, role)

    if not fault_types:
        raise ValueError(f"No fault types for {device['name']} ({platform}/{role})")

    fault_def = random.choice(fault_types)

    # Pick a concrete target (interface, neighbor, etc.) from the device spec
    detail = _pick_fault_target(device, fault_def["name"], spec)

    return Fault(
        fault_type=fault_def["name"],
        device=device["name"],
        platform=platform,
        detail=detail,
        expected_skill=fault_def["expected_skill"],
    )


def _pick_fault_target(device: dict, fault_type: str, spec: dict | None = None) -> str:
    """Pick a concrete interface/neighbor/VNI for the fault from the device spec."""
    interfaces = device.get("interfaces", [])

    if fault_type in ("shut_interface", "change_ip"):
        # Pick the first data-plane interface (has a peer)
        for iface in interfaces:
            if iface.get("peer"):
                return iface["name"]
        return interfaces[0]["name"] if interfaces else "Ethernet1"

    if fault_type == "wrong_asn":
        # Pick the first BGP neighbor IP (peer IP on first interface)
        for iface in interfaces:
            if iface.get("ipv4") and iface.get("peer"):
                ip = iface["ipv4"].split("/")[0]
                octets = ip.split(".")
                # Peer IP for /31 is the other address
                last = int(octets[3])
                return ".".join(octets[:3]) + "." + str(last ^ 1)
        return ""

    if fault_type == "remove_vni":
        # Read VNI from the device's site fabric spec — never hardcode
        if spec:
            for _sk, site in spec.get("sites", {}).items():
                site_devices = [d["name"] for d in site.get("devices", [])]
                if device["name"] in site_devices:
                    vni_mappings = site.get("fabric", {}).get("vxlan", {}).get("vni_mappings", [])
                    if vni_mappings:
                        return str(vni_mappings[0]["vni"])
        # Fallback: check device interfaces for vni field
        for iface in interfaces:
            if iface.get("vni"):
                return str(iface["vni"])
        return ""

    return ""


def verify_detection(fault: Fault, agent_result: dict) -> bool:
    """Check if the agent detected the injected fault.

    Compares the fault type against the agent's skill results for
    the target device.
    """
    skill_name = fault.expected_skill
    skill_result = agent_result.get(skill_name, {})
    device_result = skill_result.get("devices", {}).get(fault.device, {})

    if fault.fault_type == "shut_interface":
        # fabric_health: device should be unhealthy with down neighbors
        if not device_result.get("healthy", True):
            return True
        # Or branch_connectivity might catch it
        if device_result.get("down_neighbors"):
            return True

    if fault.fault_type == "wrong_asn" and not device_result.get("healthy", True):
        return True

    if fault.fault_type in ("change_ip", "remove_vni"):
        # spec_compliance: should show drifts on the affected interface
        drifts = device_result.get("drifts", [])
        if drifts:
            return True

    return False


ROLLBACK_TIMEOUT_SECONDS = 120


def rollback_fault(fault: Fault, creds: object) -> bool:
    """Rollback by pushing the clean config from configs/generated/.

    Returns True on success. Bounded by ROLLBACK_TIMEOUT_SECONDS so a
    broken device doesn't hang the finally block forever.
    """
    import signal

    from scripts.push_configs import push_config_to_device

    config_path = CONFIGS_DIR / f"{fault.device}.cfg"
    mgmt_ips = get_mgmt_ips()
    mgmt_ip = mgmt_ips.get(fault.device, "")

    if not mgmt_ip or not config_path.exists():
        logger.error("Cannot rollback %s: missing IP or config", fault.device)
        return False

    username = creds.device_username
    password = creds.device_password
    if fault.platform == "fortinet_fortios":
        username = creds.fortigate_username or username
        password = creds.fortigate_password or password

    log_file = Path("logs") / "chaos_rollback.jsonl"

    def _timeout_handler(signum: int, frame: object) -> None:
        raise TimeoutError(f"Rollback timed out after {ROLLBACK_TIMEOUT_SECONDS}s")

    # Use SIGALRM for bounded rollback (Unix only; on Windows falls through)
    old_handler = None
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(ROLLBACK_TIMEOUT_SECONDS)
    except (OSError, AttributeError):
        pass  # SIGALRM not available (Windows) — rollback runs unbounded

    try:
        return push_config_to_device(
            fault.device,
            fault.platform,
            config_path,
            mgmt_ip,
            username,
            password,
            log_file,
        )
    except TimeoutError:
        logger.error(
            "Rollback TIMED OUT for %s after %ds — device may be in faulted state",
            fault.device,
            ROLLBACK_TIMEOUT_SECONDS,
        )
        return False
    finally:
        try:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        except (OSError, AttributeError):
            pass


def run_chaos_test(
    spec: dict,
    device_filter: str = "",
    dry_run: bool = False,
    predict: bool = False,
) -> dict:
    """Run a single chaos test cycle: inject → detect → rollback.

    Rollback is guaranteed via try/finally even if the agent crashes.

    Returns:
        {fault: Fault, detected: bool, rolled_back: bool, prediction: dict|None}
    """
    fault = select_random_fault(spec, device_filter)
    result: dict = {
        "fault": fault,
        "detected": False,
        "rolled_back": False,
        "prediction": None,
    }

    print(f"  Fault: {fault.fault_type} on {fault.device} ({fault.detail})")

    if dry_run:
        print("  DRY RUN — no injection")
        return result

    creds = require_credentials("device_username", "device_password")
    conn = None

    try:
        # Inject the fault
        from netmiko import ConnectHandler

        mgmt_ips = get_mgmt_ips()
        mgmt_ip = mgmt_ips.get(fault.device, "")

        username = creds.device_username
        password = creds.device_password
        if fault.platform == "fortinet_fortios":
            username = creds.fortigate_username or username
            password = creds.fortigate_password or password

        device_type = {
            "arista_eos": "arista_eos",
            "cisco_iosxe": "cisco_xe",
            "fortinet_fortios": "fortinet",
        }.get(fault.platform, "")

        conn = ConnectHandler(
            device_type=device_type,
            host=mgmt_ip,
            username=username,
            password=password,
            secret=password,
            timeout=30,
        )
        if fault.platform != "fortinet_fortios":
            conn.enable()

        # Look up the device's actual ASN from the spec for wrong_asn faults
        from agent.runner import _find_device_in_spec

        dev_spec, _ = _find_device_in_spec(spec, fault.device)
        device_asn = dev_spec.get("asn", 0)

        cmds = build_fault_commands(
            fault.platform,
            fault.fault_type,
            interface=fault.detail,
            neighbor=fault.detail,
            wrong_asn=99999,
            wrong_ip="10.99.99.99/31",
            local_asn=device_asn,
            vni=fault.detail,
        )

        print(f"  Injecting: {cmds}")
        if fault.platform == "fortinet_fortios":
            import time

            for cmd in cmds:
                conn.write_channel(cmd + "\n")
                time.sleep(0.5)
        else:
            conn.send_config_set(cmds, cmd_verify=False)

        conn.disconnect()
        conn = None

        # Optional: predict impact via Batfish
        if predict:
            try:
                from batfish.snapshot import build_snapshot
                from batfish.validate import failure_impact, init_batfish

                snapshot_dir = build_snapshot(spec, CONFIGS_DIR, Path("batfish/snapshot"))
                bf = init_batfish(snapshot_dir)
                result["prediction"] = failure_impact(bf, fault.device, spec)
                print(f"  Batfish prediction: {result['prediction']}")
            except Exception as e:
                print(f"  Batfish prediction skipped: {e}")

        # Run the agent to detect the fault

        print("  Running agent detection...")
        # We need to capture skill results — run individual skills
        agent_results = {}
        from agent.runner import run_branch_connectivity, run_fabric_health, run_spec_compliance

        log_file = Path("logs") / "chaos_agent.jsonl"
        agent_results["fabric_health"] = run_fabric_health(spec, creds, log_file)
        agent_results["branch_connectivity"] = run_branch_connectivity(spec, creds, log_file)
        agent_results["spec_compliance"] = run_spec_compliance(spec, creds, log_file)

        result["detected"] = verify_detection(fault, agent_results)
        if result["detected"]:
            print(f"  DETECTED by {fault.expected_skill}")
        else:
            print(f"  MISSED — agent did not detect {fault.fault_type}")

    finally:
        # Guarantee rollback even if agent crashes
        if conn:
            with contextlib.suppress(Exception):
                conn.disconnect()

        if not dry_run:
            print(f"  Rolling back {fault.device}...")
            creds_rb = require_credentials("device_username", "device_password")
            result["rolled_back"] = rollback_fault(fault, creds_rb)
            if result["rolled_back"]:
                print("  Rollback: OK")
            else:
                print(
                    f"  ROLLBACK FAILED on {fault.device}. "
                    f"Manual fix: python -m scripts.push_configs --device {fault.device}"
                )

    return result


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Chaos testing for AI Infra Lab")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--device", default="", help="Target a specific device")
    parser.add_argument("--dry-run", action="store_true", help="Preview without injection")
    parser.add_argument("--predict", action="store_true", help="Include Batfish impact prediction")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)

    spec = yaml.safe_load(args.spec.read_text())

    print("Chaos Test")
    print("=" * 50)

    result = run_chaos_test(spec, args.device, args.dry_run, args.predict)

    print("=" * 50)
    fault = result["fault"]
    print(f"Fault: {fault.fault_type} on {fault.device} ({fault.detail})")
    print(f"Detected: {result['detected']}")
    print(f"Rolled back: {result['rolled_back']}")

    if not args.dry_run and not result["detected"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
