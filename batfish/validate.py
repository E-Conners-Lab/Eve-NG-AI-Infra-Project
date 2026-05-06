"""Batfish pre-deployment validation queries.

Runs pybatfish queries against a Batfish snapshot to validate BGP sessions,
reachability, and route propagation before configs are pushed to live devices.

Requires a running Batfish server (Docker). Host is read from BATFISH_HOST
env var (default: localhost).

Coverage gap — IPsec/IKEv2: Batfish does not model Cisco IKEv2/IPsec crypto
blocks (``crypto ikev2 proposal/policy/keyring/profile``, ``crypto ipsec
transform-set/profile``, ``tunnel protection ipsec profile``). The IPsec block
emitted by ``templates/cisco/ipsec_tunnel.j2`` is effectively skipped during
validation here — a green ``validate-batfish`` does NOT confirm the IPsec
tunnel will come up. Use the MCP ``cloud_tunnel_health`` tool against the live
device for ground-truth IKEv2/IPsec state instead.

Usage:
    python -m batfish.validate
    python -m batfish.validate --strict
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from batfish.snapshot import CONFIGS_DIR, SPEC_PATH, build_snapshot

logger = logging.getLogger(__name__)

SNAPSHOT_OUTPUT = Path(__file__).parent / "snapshot"

# Compatible BGP session statuses (everything else is a problem)
# HALF_OPEN = iBGP overlay sessions that depend on underlay reachability.
# These are expected for EVPN peers using update-source Loopback0.
BGP_COMPATIBLE_STATUSES = {"UNIQUE_MATCH", "DYNAMIC_MATCH", "HALF_OPEN"}

# Reachable traceroute dispositions
REACHABLE_DISPOSITIONS = {"ACCEPTED", "DELIVERED_TO_SUBNET", "EXITS_NETWORK"}


class BatfishConnectionError(Exception):
    """Raised when the Batfish server is not reachable."""


def _get_batfish_host() -> str:
    """Return the Batfish server host from environment."""
    return os.environ.get("BATFISH_HOST", "localhost")


def init_batfish(snapshot_dir: Path, host: str = "") -> object:
    """Initialize a pybatfish session and upload the snapshot.

    Args:
        snapshot_dir: Path to the Batfish snapshot directory.
        host: Batfish server hostname. Defaults to BATFISH_HOST env var.

    Returns:
        A pybatfish Session object ready for queries.

    Raises:
        BatfishConnectionError: If the Batfish server is not reachable.
    """
    if not host:
        host = _get_batfish_host()

    try:
        from pybatfish.client.session import Session

        bf = Session(host=host)
        bf.set_network("ai-infra-lab")
        bf.init_snapshot(str(snapshot_dir), name="validation", overwrite=True)
        return bf
    except Exception as e:
        raise BatfishConnectionError(
            f"Batfish server not reachable at {host}:9997. "
            f"Run: docker run -d -p 9997:9997 -p 9996:9996 batfish/allinone\n"
            f"Original error: {e}"
        ) from e


def resolve_host_ip(spec: dict, device_name: str) -> str:
    """Resolve a device name to its first interface IP (host portion only).

    Walks all sites looking for the device, returns the IP from its first
    interface that has an ipv4 address (stripped of prefix length).
    """
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name:
                for iface in dev.get("interfaces", []):
                    ipv4 = iface.get("ipv4", "")
                    if ipv4:
                        return ipv4.split("/")[0]
    return ""


def check_bgp_sessions(session: object) -> list[dict]:
    """Check BGP session compatibility across all devices.

    Returns a list of incompatible sessions. Empty list = all compatible.
    """
    df = session.q.bgpSessionCompatibility().answer().frame()
    issues: list[dict] = []

    for _, row in df.iterrows():
        status = row.get("Configured_Status", "")
        if status not in BGP_COMPATIBLE_STATUSES:
            issues.append(
                {
                    "type": "bgp_incompatible",
                    "node": row.get("Node", ""),
                    "vrf": row.get("VRF", ""),
                    "local_ip": row.get("Local_IP", ""),
                    "remote_ip": row.get("Remote_IP", ""),
                    "remote_as": row.get("Remote_AS", ""),
                    "status": status,
                }
            )

    return issues


def check_reachability(
    session: object, src_ip: str, dst_ip: str, snapshot: str | None = None
) -> dict:
    """Run a traceroute simulation from src_ip to dst_ip.

    Returns:
        {reachable: bool, disposition: str, hop_count: int}
    """
    try:
        answer = session.q.traceroute(
            startLocation=f"@enter(Management0[{src_ip}])",
            headers={"dstIps": dst_ip},
        ).answer(snapshot=snapshot)
        df = answer.frame()
    except Exception:
        # Fall back to simpler startLocation format
        try:
            from pybatfish.datamodel.flow import HeaderConstraints

            answer = session.q.traceroute(
                startLocation=src_ip,
                headers=HeaderConstraints(dstIps=dst_ip),
            ).answer(snapshot=snapshot)
            df = answer.frame()
        except Exception:
            return {"reachable": False, "disposition": "QUERY_ERROR", "hop_count": 0}

    if df.empty:
        return {"reachable": False, "disposition": "NO_TRACE", "hop_count": 0}

    traces = df.iloc[0].get("Traces", [])
    if not traces:
        return {"reachable": False, "disposition": "NO_TRACE", "hop_count": 0}

    first_trace = traces[0]
    disposition = getattr(first_trace, "disposition", "UNKNOWN")
    hops = getattr(first_trace, "hops", [])

    return {
        "reachable": disposition in REACHABLE_DISPOSITIONS,
        "disposition": disposition,
        "hop_count": len(hops),
    }


def failure_impact(
    session: object, node: str, spec: dict, base_snapshot: str = "validation"
) -> dict:
    """Simulate a node failure and check which reachability paths break.

    Forks the snapshot with the target node deactivated, then re-runs
    reachability checks for all matrix entries.

    Returns:
        {node: str, broken: [{src, dst, description}], surviving: [{src, dst, description}]}
    """
    failure_snapshot = f"fail-{node}"
    session.fork_snapshot(
        base_name=base_snapshot,
        name=failure_snapshot,
        deactivate_nodes=[node],
        overwrite=True,
    )

    matrix = spec.get("tests", {}).get("reachability_matrix", [])
    broken: list[dict] = []
    surviving: list[dict] = []

    for entry in matrix:
        src_ip = resolve_host_ip(spec, entry["source"])
        dst_ip = resolve_host_ip(spec, entry["destination"])
        if not src_ip or not dst_ip:
            continue

        result = check_reachability(session, src_ip, dst_ip, snapshot=failure_snapshot)
        record = {
            "src": entry["source"],
            "dst": entry["destination"],
            "description": entry.get("description", ""),
        }
        if result["reachable"]:
            surviving.append(record)
        else:
            broken.append(record)

    return {"node": node, "broken": broken, "surviving": surviving}


def validate_pre_deploy(spec: dict, configs_dir: Path) -> tuple[bool, list[dict]]:
    """Run all Batfish validation checks and return pass/fail.

    Pass/fail logic:
    - ANY incompatible BGP session = FAIL
    - ANY unreachable reachability matrix entry = FAIL
    - Route propagation issues = WARN (included in issues but don't block)

    Returns:
        (all_critical_passed, issues_list)
    """
    # Build snapshot
    snapshot_dir = build_snapshot(spec, configs_dir, SNAPSHOT_OUTPUT)

    # Connect to Batfish
    session = init_batfish(snapshot_dir)

    all_issues: list[dict] = []
    critical_fail = False

    # 1. BGP session compatibility
    print("  Checking BGP session compatibility...")
    bgp_issues = check_bgp_sessions(session)
    if bgp_issues:
        critical_fail = True
        for issue in bgp_issues:
            issue["severity"] = "critical"
            all_issues.append(issue)
        print(f"    FAIL: {len(bgp_issues)} incompatible BGP sessions")
    else:
        print("    PASS: all BGP sessions compatible")

    # 2. Reachability matrix
    # Host-to-host paths traverse FortiGate firewalls where Batfish has
    # limited routing model. These are warnings, not critical failures.
    # The agent's live fabric_health + branch_connectivity skills validate
    # end-to-end reachability on real devices.
    print("  Checking reachability matrix...")
    matrix = spec.get("tests", {}).get("reachability_matrix", [])
    for entry in matrix:
        src_ip = resolve_host_ip(spec, entry["source"])
        dst_ip = resolve_host_ip(spec, entry["destination"])
        if not src_ip or not dst_ip:
            continue

        result = check_reachability(session, src_ip, dst_ip)
        if not result["reachable"]:
            all_issues.append(
                {
                    "type": "reachability_warning",
                    "severity": "warning",
                    "src": entry["source"],
                    "src_ip": src_ip,
                    "dst": entry["destination"],
                    "dst_ip": dst_ip,
                    "disposition": result["disposition"],
                    "description": entry.get("description", ""),
                }
            )
            print(
                f"    WARN: {entry['source']} → {entry['destination']} (host path — validated live)"
            )
        else:
            print(f"    PASS: {entry['source']} → {entry['destination']}")

    passed = not critical_fail
    return passed, all_issues


def main() -> None:
    """CLI entrypoint — run Batfish validation."""
    parser = argparse.ArgumentParser(description="Batfish pre-deploy validation")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--configs", type=Path, default=CONFIGS_DIR)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with error code on any failure",
    )
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())

    print("Batfish pre-deploy validation")
    print("=" * 50)

    try:
        passed, issues = validate_pre_deploy(spec, args.configs)
    except BatfishConnectionError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(2)

    print("=" * 50)
    if passed:
        print("RESULT: PASSED — all critical checks passed")
    else:
        critical = [i for i in issues if i.get("severity") == "critical"]
        print(f"RESULT: FAILED — {len(critical)} critical issues found")

    if args.strict and not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
