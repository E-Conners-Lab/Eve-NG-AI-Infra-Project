"""CLI for the troubleshooting framework.

    python -m troubleshooting list
    python -m troubleshooting show <name>
    python -m troubleshooting inject <name>
    python -m troubleshooting status <name>
    python -m troubleshooting fix <name>
    python -m troubleshooting restore <name>
    python -m troubleshooting runbook <name>

`status` deliberately does not reveal *what* was injected — only whether a
fault is currently present. Read the runbook (or run `fix`) when you're done.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

# Importing the scenarios package registers every scenario via side effects.
import troubleshooting.scenarios  # noqa: E402, F401
from troubleshooting._common import REGISTRY, connect_device, restore_clean_config

PROJECT_ROOT = Path(__file__).parent.parent


def _resolve_scenario(name: str):
    try:
        return REGISTRY.get(name)
    except KeyError:
        print(f"unknown scenario: {name}", file=sys.stderr)
        return None


def cmd_list(_args: argparse.Namespace) -> int:
    scenarios = REGISTRY.all()
    if not scenarios:
        print("(no scenarios registered)")
        return 0
    width = max(len(s.name) for s in scenarios)
    for s in scenarios:
        print(f"  {s.name:<{width}}  [{s.difficulty:<12}] {s.device:<12}  {s.symptoms}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    scenario = _resolve_scenario(args.name)
    if not scenario:
        return 2
    print(f"name        : {scenario.name}")
    print(f"device      : {scenario.device}")
    print(f"platform    : {scenario.platform}")
    print(f"difficulty  : {scenario.difficulty}")
    print(f"symptoms    : {scenario.symptoms}")
    print(f"runbook     : {scenario.runbook}")
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    scenario = _resolve_scenario(args.name)
    if not scenario:
        return 2
    print(f"Injecting fault on {scenario.device}...")
    conn = connect_device(scenario.device, platform=scenario.platform)
    try:
        scenario.inject(conn)
    finally:
        with contextlib.suppress(Exception):
            conn.disconnect()
    print(f"Fault injected. Run `python -m troubleshooting status {scenario.name}` to verify.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    scenario = _resolve_scenario(args.name)
    if not scenario:
        return 2
    conn = connect_device(scenario.device, platform=scenario.platform)
    try:
        present, evidence = scenario.detect(conn)
    finally:
        with contextlib.suppress(Exception):
            conn.disconnect()
    if present:
        print(f"FAULT PRESENT on {scenario.device}: {evidence}")
        return 1
    print(f"NO FAULT detected on {scenario.device}: {evidence}")
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    scenario = _resolve_scenario(args.name)
    if not scenario:
        return 2
    print(f"Applying fix on {scenario.device}...")
    conn = connect_device(scenario.device, platform=scenario.platform)
    try:
        scenario.fix(conn)
    finally:
        with contextlib.suppress(Exception):
            conn.disconnect()
    print("Fix applied.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    scenario = _resolve_scenario(args.name)
    if not scenario:
        return 2
    print(f"Restoring clean config on {scenario.device} (full push)...")
    ok = restore_clean_config(scenario.device, platform=scenario.platform)
    print("Restore: OK" if ok else "Restore FAILED")
    return 0 if ok else 1


def cmd_runbook(args: argparse.Namespace) -> int:
    scenario = _resolve_scenario(args.name)
    if not scenario:
        return 2
    rb_path = Path(scenario.runbook)
    if not rb_path.is_absolute():
        rb_path = PROJECT_ROOT / rb_path
    if not rb_path.exists():
        print(f"runbook missing: {rb_path}", file=sys.stderr)
        return 2
    print(rb_path.read_text())
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="troubleshooting")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list registered scenarios").set_defaults(func=cmd_list)

    for verb, fn, helptext in (
        ("show", cmd_show, "print scenario metadata (no spoilers)"),
        ("inject", cmd_inject, "inject the fault on the live device"),
        ("status", cmd_status, "check whether the fault is currently present"),
        ("fix", cmd_fix, "apply the targeted repair (the answer)"),
        ("restore", cmd_restore, "push the clean spec config (full reset)"),
        ("runbook", cmd_runbook, "print the runbook markdown for the scenario"),
    ):
        sp = sub.add_parser(verb, help=helptext)
        sp.add_argument("name")
        sp.set_defaults(func=fn)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
