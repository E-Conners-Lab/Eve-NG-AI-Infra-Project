"""Run the reachability test matrix from the YAML spec.

SSHes to source hosts and pings each destination. Reports pass/fail
for each path. Exits non-zero if any path fails.

Usage:
    python -m scripts.run_reachability
    python -m scripts.run_reachability --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"


def _ssh_ping(
    host_name: str,
    host_ip: str,
    dest_ip: str,
    username: str,
    password: str,
    count: int = 3,
) -> bool:
    """SSH to a host and run ping. Returns True if reachable."""
    try:
        conn = Scrapli(
            host=host_ip,
            auth_username=username,
            auth_password=password,
            auth_strict_key=False,
            platform="linux",
            transport="system",
        )
        conn.open()
        result = conn.send_command(f"ping -c {count} -W 2 {dest_ip}")
        conn.close()
        return f"{count} packets received" in result.result or "0% packet loss" in result.result
    except ScrapliException:
        return False


def _resolve_host_ip(spec: dict, host_name: str) -> str:
    """Find a host's data-plane IP from its interfaces in the spec."""
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == host_name:
                for iface in dev.get("interfaces", []):
                    if "ipv4" in iface:
                        return iface["ipv4"].split("/")[0]
    return ""


def run_reachability(spec: dict, dry_run: bool = False) -> tuple[int, int]:
    """Run all reachability tests. Returns (pass_count, fail_count)."""
    matrix = spec.get("tests", {}).get("reachability_matrix", [])
    if not matrix:
        print("No reachability tests defined in spec.")
        return 0, 0

    mgmt_ips = get_mgmt_ips()
    creds = None
    if not dry_run:
        creds = require_credentials("device_username", "device_password")

    passed, failed = 0, 0
    print(f"Running {len(matrix)} reachability tests:\n")

    for test in matrix:
        src = test["source"]
        dst = test["destination"]
        desc = test["description"]

        src_mgmt = mgmt_ips.get(src, "")
        dst_data_ip = _resolve_host_ip(spec, dst)

        print(f"  {src} -> {dst} ({desc})")
        print(f"    SSH to {src} ({src_mgmt}), ping {dst} ({dst_data_ip})", end="")

        if not src_mgmt:
            print(" — SKIP (no mgmt IP for source)")
            failed += 1
            continue
        if not dst_data_ip:
            print(" — SKIP (no data IP for destination)")
            failed += 1
            continue

        if dry_run:
            print(" — DRY RUN")
            passed += 1
            continue

        ok = _ssh_ping(src, src_mgmt, dst_data_ip, creds.device_username, creds.device_password)
        if ok:
            print(" — PASS")
            passed += 1
        else:
            print(" — FAIL")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed out of {len(matrix)} tests")
    return passed, failed


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Run reachability tests from spec")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Preview without running pings")
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())
    _passed, failed = run_reachability(spec, args.dry_run)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
