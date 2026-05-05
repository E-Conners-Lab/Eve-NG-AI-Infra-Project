"""Parallel BGP summary collection across all managed Arista + Cisco devices.

Demonstrates the layered toolchain:
  pyATS testbed (inventory)  ->  Nornir (parallel)  ->  netmiko (transport)
                            ->  per-vendor parser  ->  normalized dict

Usage:
    .venv/bin/python -m automation.runbooks.check_bgp_summary
    .venv/bin/python -m automation.runbooks.check_bgp_summary --json
"""

from __future__ import annotations

import argparse
import json
import sys

from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command

from automation.bgp_state import parse_bgp_summary
from automation.inventory import init_nornir


def gather_bgp_summary(task: Task) -> Result:
    """Collect raw BGP summary on this host and parse to normalized schema."""
    os_name = task.host.data.get("os", "")
    if os_name not in ("eos", "iosxe"):
        return Result(host=task.host, result={"skipped": f"unsupported os: {os_name}"})

    sub = task.run(task=netmiko_send_command, command_string="show ip bgp summary")
    raw = str(sub[0].result)
    summary = parse_bgp_summary(os_name=os_name, raw=raw)
    return Result(host=task.host, result=summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel BGP summary across the managed fabric")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON instead of human table")
    parser.add_argument("--role", default="managed", help='Inventory filter (default "managed")')
    args = parser.parse_args()

    nr = init_nornir(role=args.role)
    target = nr.filter(filter_func=lambda h: h.platform in ("arista_eos", "cisco_xe"))
    if not target.inventory.hosts:
        print("No matching hosts.", file=sys.stderr)
        return 1

    result = target.run(task=gather_bgp_summary)

    output: dict[str, dict] = {}
    for name in sorted(result):
        mr = result[name]
        if mr.failed:
            output[name] = {"error": str(mr.exception)}
        else:
            # Nornir multi-task aggregation: result[0] is parent; the parsed dict is its .result
            output[name] = mr[0].result if isinstance(mr[0].result, dict) else {"raw": mr[0].result}

    if args.json:
        print(json.dumps(output, indent=2, default=str))
        return 0

    # Human-readable summary
    total_neighbors = 0
    total_established = 0
    for name in sorted(output):
        data = output[name]
        if "error" in data:
            print(f"{name:14s}  ERROR: {data['error']}")
            continue
        nbrs = data.get("neighbors", [])
        est = sum(1 for n in nbrs if n.get("state") == "Established")
        total_neighbors += len(nbrs)
        total_established += est
        print(f"{name:14s}  AS {data.get('local_as', '?'):<6}  {est}/{len(nbrs)} Established")
        for n in nbrs:
            state = n.get("state", "?")
            mark = "✓" if state == "Established" else "✗"
            print(
                f"   {mark} {n['neighbor']:14s}  AS {n.get('remote_as', '?'):<6}  "
                f"{state}  prefixes={n.get('prefixes_received', '-')}"
            )
    print(
        f"\nTotal: {total_established}/{total_neighbors} sessions Established "
        f"across {len(output)} devices"
    )
    return 0 if total_established == total_neighbors else 2


if __name__ == "__main__":
    sys.exit(main())
