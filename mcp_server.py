"""AI Infra Lab MCP Server.

Exposes the agent's capabilities as MCP tools so Claude Code can
interactively query device state, run compliance checks, validate
configs via Batfish, and trigger chaos tests.

Concurrency control:
- Global semaphore limits concurrent SSH sessions (default: 4)
- Per-device locks prevent overlapping SSH to the same device
- Sync Netmiko calls wrapped in asyncio.to_thread

Tools:
    - check_fabric_health: BGP health on Arista fabric devices
    - check_branch_connectivity: Dual-homed BGP on CE routers
    - check_spec_compliance: Config drift detection vs spec
    - run_batfish_validation: Pre-deploy config validation
    - run_chaos_test: Fault injection + detection + rollback
    - get_device_state: Live show commands from a single device
    - get_topology: Lab topology summary from the spec (no SSH)
    - blast_radius: Batfish failure impact analysis for a node

Usage:
    python mcp_server.py          # stdio (Claude Code integration)
    python mcp_server.py --sse    # SSE server on localhost:8401
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

SPEC_PATH = Path(__file__).parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent / "configs" / "generated"
LOG_DIR = Path(__file__).parent / "logs"

# ---------------------------------------------------------------------------
# Concurrency control
# ---------------------------------------------------------------------------
MAX_CONCURRENT_DEVICES = 10
_device_semaphore: asyncio.Semaphore | None = None
_device_locks: dict[str, asyncio.Lock] = {}
_batfish_lock: asyncio.Lock | None = None

# Threading lock guards lazy init of asyncio primitives so concurrent
# coroutines on the first call can't create duplicates.
_init_lock = threading.Lock()


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy semaphore creation — thread-safe for first call."""
    global _device_semaphore
    if _device_semaphore is None:
        with _init_lock:
            if _device_semaphore is None:
                _device_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DEVICES)
    return _device_semaphore


def _get_device_lock(device: str) -> asyncio.Lock:
    """Return a per-device lock, creating it if needed.

    Prevents overlapping SSH sessions to the same device when
    multiple MCP tool calls run concurrently. Thread-safe init.
    """
    if device not in _device_locks:
        with _init_lock:
            if device not in _device_locks:
                _device_locks[device] = asyncio.Lock()
    return _device_locks[device]


def _get_batfish_lock() -> asyncio.Lock:
    """Return the Batfish operation lock (lazy creation, thread-safe).

    Serializes snapshot builds and fork_snapshot calls so concurrent
    blast_radius or run_batfish_validation calls don't corrupt each other.
    """
    global _batfish_lock
    if _batfish_lock is None:
        with _init_lock:
            if _batfish_lock is None:
                _batfish_lock = asyncio.Lock()
    return _batfish_lock


async def _run_with_device_limit(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread with semaphore throttling."""
    async with _get_semaphore():
        return await asyncio.to_thread(fn, *args, **kwargs)


async def _run_with_device_lock(device: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function with both the global semaphore and a per-device lock.

    The per-device lock prevents two tool calls from opening concurrent SSH
    sessions to the same device (which corrupts Netmiko's read buffer).
    The global semaphore caps total concurrent SSH sessions.
    """
    async with _get_device_lock(device), _get_semaphore():
        return await asyncio.to_thread(fn, *args, **kwargs)


async def _run_with_batfish_lock(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a Batfish operation under the Batfish lock.

    Serializes snapshot builds and fork_snapshot calls to prevent
    concurrent writes to the shared snapshot directory.
    """
    async with _get_batfish_lock():
        return await asyncio.to_thread(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_spec() -> dict:
    """Load the current lab spec (fresh on every call)."""
    return yaml.safe_load(SPEC_PATH.read_text())


def _load_creds() -> object:
    """Load credentials from .env."""
    from scripts.credentials import require_credentials

    return require_credentials("device_username", "device_password")


def _log_file() -> Path:
    """Return a GAIT log path for MCP tool calls."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / "mcp_tools.jsonl"


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="AI Infra Lab",
    instructions=(
        "You are connected to a 21-node multi-site network lab running in EVE-NG. "
        "3 sites (DC-East, Branch-01, DR-West), 8 Arista vEOS, 5 Cisco C8000v, "
        "4 FortiGate firewalls, 4 Alpine Linux hosts. Use these tools to check "
        "device health, detect config drift, validate changes via Batfish, and "
        "run chaos tests. All tools connect to live devices via SSH."
    ),
    version="0.1.0",
)


# ── Tool: check_fabric_health ──


def _run_fabric_health(device: str = "") -> dict[str, Any]:
    """Sync wrapper for fabric health check."""
    from agent.runner import run_fabric_health

    spec = _load_spec()
    creds = _load_creds()
    result = run_fabric_health(spec, creds, _log_file())

    if device:
        device_result = result.get("devices", {}).get(device)
        if device_result is None:
            return {"error": f"Device '{device}' not found in fabric check results"}
        return {"device": device, **device_result}

    return result


@mcp.tool()
async def check_fabric_health(device: str = "") -> dict[str, Any]:
    """Check BGP fabric health on managed Arista devices.

    Validates eBGP underlay sessions between spines, leaves, and borders
    in DC-East and DR-West fabrics. Reports established/down neighbors
    per device.

    Args:
        device: Optional device name (e.g., dc-spine-1, dc-leaf-1).
            If omitted, checks all fabric devices.
    """
    return await _run_with_device_limit(_run_fabric_health, device)


# ── Tool: check_branch_connectivity ──


def _run_branch_connectivity() -> dict[str, Any]:
    """Sync wrapper for branch connectivity check."""
    from agent.runner import run_branch_connectivity

    spec = _load_spec()
    creds = _load_creds()
    return run_branch_connectivity(spec, creds, _log_file())


@mcp.tool()
async def check_branch_connectivity() -> dict[str, Any]:
    """Check dual-homed BGP on CE routers (dc-ce-1, br-ce-1, dr-ce-1).

    Each CE router should have 2 established BGP sessions to the service
    provider PE routers (sp-pe-1, sp-pe-2). Reports session state and
    prefix counts per CE device.
    """
    return await _run_with_device_limit(_run_branch_connectivity)


# ── Tool: check_spec_compliance ──


def _run_spec_compliance(device: str = "") -> dict[str, Any]:
    """Sync wrapper for spec compliance check."""
    from agent.runner import run_spec_compliance

    spec = _load_spec()
    creds = _load_creds()
    result = run_spec_compliance(spec, creds, _log_file())

    if device:
        device_result = result.get("devices", {}).get(device)
        if device_result is None:
            return {"error": f"Device '{device}' not found in compliance check results"}
        return {"device": device, **device_result}

    return result


@mcp.tool()
async def check_spec_compliance(device: str = "") -> dict[str, Any]:
    """Check config drift between spec and live device state.

    Compares interface IPs and BGP ASNs on all managed devices against
    the declared YAML spec. Reports exact drifts (expected vs live) per
    interface.

    Args:
        device: Optional device name (e.g., dc-spine-1, dc-fw-1).
            If omitted, checks all 15 managed network devices.
    """
    return await _run_with_device_limit(_run_spec_compliance, device)


# ── Tool: run_batfish_validation ──


@mcp.tool()
async def run_batfish_validation() -> dict[str, Any]:
    """Run Batfish pre-deploy validation on generated configs.

    Builds a Batfish snapshot from the current configs and spec topology,
    then checks BGP session compatibility and reachability. Requires the
    Batfish Docker container running on localhost:9997.

    No SSH to devices — this is an offline config analysis.
    """
    from batfish.validate import BatfishConnectionError, validate_pre_deploy

    spec = _load_spec()
    try:
        passed, issues = await _run_with_batfish_lock(validate_pre_deploy, spec, CONFIGS_DIR)
        return {
            "passed": passed,
            "issue_count": len(issues),
            "critical": [i for i in issues if i.get("severity") == "critical"],
            "warnings": [i for i in issues if i.get("severity") == "warning"],
        }
    except BatfishConnectionError as e:
        return {
            "passed": False,
            "error": str(e),
            "hint": "Run: docker start batfish",
        }


# ── Tool: run_chaos_test ──


def _run_chaos(device: str, dry_run: bool) -> dict[str, Any]:
    """Sync wrapper for chaos test."""
    from scripts.chaos_test import run_chaos_test

    spec = _load_spec()
    result = run_chaos_test(spec, device_filter=device, dry_run=dry_run)

    # Serialize the Fault dataclass for JSON
    fault = result.get("fault")
    if fault:
        result["fault"] = {
            "type": fault.fault_type,
            "device": fault.device,
            "platform": fault.platform,
            "detail": fault.detail,
            "expected_skill": fault.expected_skill,
        }

    # Flag rollback failures so the caller knows the device is still faulted
    if not dry_run and not result.get("rolled_back", False):
        device_name = fault.device if fault else device
        result["severity"] = "critical"
        result["rollback_failed"] = True
        result["manual_fix"] = (
            f"Device {device_name} is in a FAULTED STATE. "
            f"Run: python -m scripts.push_configs --device {device_name}"
        )

    return result


@mcp.tool()
async def run_chaos_test(device: str = "", dry_run: bool = True) -> dict[str, Any]:
    """Inject a fault into a live device, verify agent detection, rollback.

    Selects a random managed device and fault type (shut interface, wrong ASN,
    change IP, or remove VNI). Injects the fault, runs the agent to verify
    detection, then pushes the clean config to rollback.

    SAFE BY DEFAULT: dry_run=true only previews — no injection happens.
    Set dry_run=false to actually inject a fault.

    Args:
        device: Target device name (random if omitted).
        dry_run: If true (default), preview only — no SSH, no injection.
    """
    return await _run_with_device_limit(_run_chaos, device, dry_run)


# ── Tool: get_device_state ──


SHOW_COMMANDS: dict[str, dict[str, str]] = {
    "arista_eos": {
        "bgp": "show ip bgp summary",
        "interfaces": "show ip interface brief",
        "routes": "show ip route",
        "version": "show version",
    },
    "cisco_iosxe": {
        "bgp": "show ip bgp summary",
        "interfaces": "show ip interface brief",
        "routes": "show ip route",
        "version": "show version",
    },
    "fortinet_fortios": {
        "bgp": "get router info bgp summary",
        "interfaces": "get system interface physical",
        "routes": "get router info routing-table all-routes",
        "version": "get system status",
    },
}

VALID_CHECKS = {"bgp", "interfaces", "routes", "version"}


def _run_device_state(device: str, checks: list[str]) -> dict:
    """SSH to a device and run the requested show commands."""
    from agent.runner import NETMIKO_DEVICE_TYPE, _connect, _find_device_in_spec
    from scripts.bootstrap_config import get_mgmt_ips

    spec = _load_spec()
    creds = _load_creds()

    dev, platform = _find_device_in_spec(spec, device)
    if not dev:
        return {"error": f"Device '{device}' not found in spec"}

    mgmt_ips = get_mgmt_ips()
    mgmt_ip = mgmt_ips.get(device, "")
    if not mgmt_ip:
        return {"error": f"No management IP for '{device}'"}

    if platform not in NETMIKO_DEVICE_TYPE:
        return {"error": f"Unsupported platform '{platform}' for '{device}'"}

    username = creds.device_username
    password = creds.device_password
    if platform == "fortinet_fortios":
        username = getattr(creds, "fortigate_username", None) or username
        password = getattr(creds, "fortigate_password", None) or password

    conn = _connect(device, platform, mgmt_ip, username, password)
    if not conn:
        return {"error": f"SSH connection failed to '{device}' ({mgmt_ip})"}

    platform_cmds = SHOW_COMMANDS.get(platform, {})
    results: dict = {"device": device, "platform": platform, "mgmt_ip": mgmt_ip}

    try:
        for check in checks:
            cmd = platform_cmds.get(check)
            if not cmd:
                results[check] = {"error": f"No command for '{check}' on '{platform}'"}
                continue
            if platform == "fortinet_fortios":
                output = conn.send_command_timing(cmd)
            else:
                output = conn.send_command(cmd)
            results[check] = output
    finally:
        conn.disconnect()

    return results


@mcp.tool()
async def get_device_state(
    device: str,
    checks: list[str] | None = None,
) -> dict:
    """Collect live state from a managed device via SSH.

    Runs show commands on a single device and returns raw output per check.
    Supports Arista EOS, Cisco IOS-XE, and FortiOS platforms.

    Args:
        device: Device name (e.g., dc-spine-1, br-ce-1, dc-fw-1).
        checks: Which state to collect. Defaults to all.
            Options: bgp, interfaces, routes, version
    """
    check_list = checks or list(VALID_CHECKS)
    invalid = set(check_list) - VALID_CHECKS
    if invalid:
        return {"error": f"Invalid checks: {invalid}. Valid: {sorted(VALID_CHECKS)}"}
    return await _run_with_device_lock(device, _run_device_state, device, check_list)


# ── Tool: get_topology ──


@mcp.tool()
async def get_topology() -> dict:
    """Get the lab topology summary from the YAML spec.

    Returns sites, devices per site, roles, link counts, agent boundary,
    and the reachability test matrix. No SSH — pure spec read.
    """
    spec = _load_spec()

    sites: dict = {}
    all_links = 0

    for site_key, site in spec.get("sites", {}).items():
        devices = []
        for dev in site.get("devices", []):
            devices.append(
                {
                    "name": dev["name"],
                    "role": dev.get("role", ""),
                    "platform": dev.get("platform", ""),
                    "asn": dev.get("asn"),
                    "loopback0": dev.get("loopback0"),
                    "interface_count": len(dev.get("interfaces", [])),
                }
            )
        site_links = site.get("links", [])
        all_links += len(site_links)
        sites[site_key] = {
            "device_count": len(devices),
            "devices": devices,
            "link_count": len(site_links),
        }

    # WAN transport (excluded from agent boundary, but part of topology)
    wan_devices = []
    for dev in spec.get("wan_transport", {}).get("devices", []):
        wan_devices.append(
            {
                "name": dev["name"],
                "role": dev.get("role", ""),
                "platform": dev.get("platform", ""),
                "asn": dev.get("asn"),
            }
        )
    wan_links = spec.get("wan_transport", {}).get("links", [])
    all_links += len(wan_links)

    # Security (firewalls)
    firewalls: list[dict] = []
    for zone_key, zone in spec.get("security", {}).items():
        for fw in zone.get("firewalls", []):
            firewalls.append(
                {
                    "name": fw["name"],
                    "role": fw.get("role", ""),
                    "platform": fw.get("platform", ""),
                    "zone": zone_key,
                    "interface_count": len(fw.get("interfaces", [])),
                }
            )

    boundary = spec.get("agent", {}).get("boundary", {})
    matrix = spec.get("tests", {}).get("reachability_matrix", [])

    return {
        "sites": sites,
        "wan_transport": {"devices": wan_devices, "link_count": len(wan_links)},
        "firewalls": firewalls,
        "total_links": all_links,
        "agent_boundary": {
            "managed": boundary.get("managed", []),
            "observed": boundary.get("observed", []),
            "excluded": boundary.get("excluded", []),
        },
        "reachability_matrix": matrix,
    }


# ── Tool: blast_radius ──


def _run_blast_radius(node: str) -> dict:
    """Sync wrapper for Batfish failure impact analysis."""
    from batfish.snapshot import build_snapshot
    from batfish.validate import SNAPSHOT_OUTPUT, failure_impact, init_batfish

    spec = _load_spec()

    # Validate node exists in spec
    from agent.runner import _find_device_in_spec

    dev, _ = _find_device_in_spec(spec, node)
    if not dev:
        return {"error": f"Device '{node}' not found in spec"}

    snapshot_dir = build_snapshot(spec, CONFIGS_DIR, SNAPSHOT_OUTPUT)
    session = init_batfish(snapshot_dir)
    return failure_impact(session, node, spec)


@mcp.tool()
async def blast_radius(node: str) -> dict:
    """Analyze what breaks if a device goes down.

    Uses Batfish to fork the config snapshot with the target node deactivated,
    then re-runs the reachability matrix to identify broken and surviving paths.
    Requires Batfish Docker running on localhost:9997.

    No SSH to devices — this is an offline simulation.

    Args:
        node: Device name to simulate failure for (e.g., dc-spine-1, dc-fw-1).
    """
    from batfish.validate import BatfishConnectionError

    try:
        return await _run_with_batfish_lock(_run_blast_radius, node)
    except BatfishConnectionError as e:
        return {
            "node": node,
            "error": str(e),
            "hint": "Run: docker start batfish",
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server."""
    import sys

    if "--sse" in sys.argv:
        port = 8401
        logger.info("MCP server starting on SSE port %d", port)
        mcp.run(transport="sse", host="127.0.0.1", port=port)
    else:
        logger.info("MCP server starting on stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
