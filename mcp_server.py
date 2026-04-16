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

Usage:
    python mcp_server.py          # stdio (Claude Code integration)
    python mcp_server.py --sse    # SSE server on port 8401
"""

from __future__ import annotations

import asyncio
import logging
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
MAX_CONCURRENT_DEVICES = 4
_device_semaphore: asyncio.Semaphore | None = None
_device_locks: dict[str, asyncio.Lock] = {}


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy semaphore creation (must be called in async context)."""
    global _device_semaphore
    if _device_semaphore is None:
        _device_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DEVICES)
    return _device_semaphore


async def _run_with_device_limit(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread with semaphore throttling."""
    async with _get_semaphore():
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
        passed, issues = await asyncio.to_thread(validate_pre_deploy, spec, CONFIGS_DIR)
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server."""
    import sys

    if "--sse" in sys.argv:
        port = 8401
        logger.info("MCP server starting on SSE port %d", port)
        mcp.run(transport="sse", host="0.0.0.0", port=port)
    else:
        logger.info("MCP server starting on stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
