"""Run the reachability test matrix from the YAML spec.

Connects to source hosts and pings each destination. Tries SSH first,
falls back to EVE-NG telnet console if SSH management is unreachable.
Reports pass/fail for each path. Exits non-zero if any path fails.

Usage:
    python -m scripts.run_reachability
    python -m scripts.run_reachability --dry-run
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

import paramiko
import yaml

from scripts.bootstrap_config import get_mgmt_ips
from scripts.credentials import load_credentials, require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"


def _get_console_ports() -> dict[str, int]:
    """Query EVE-NG API for host node telnet console ports."""
    try:
        from scripts.eve_client import EveNgClient

        creds = load_credentials()
        if not creds.eve_ng_host or not creds.eve_ng_password:
            return {}
        client = EveNgClient(
            creds.eve_ng_host,
            creds.eve_ng_username,
            creds.eve_ng_password,
        )
        client.login()
        nodes = client.get_nodes("/AI-Infra-Lab.unl")
        ports: dict[str, int] = {}
        for _nid, node in nodes.items():
            name = node.get("name", "")
            url = node.get("url", "")
            if "host" in name and "telnet://" in url:
                port = int(url.split(":")[-1])
                ports[name] = port
        return ports
    except Exception:
        return {}


def _strip_iac(data: bytes) -> bytes:
    """Strip telnet IAC negotiation sequences from a byte stream."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b != 0xFF or i + 1 >= n:
            out.append(b)
            i += 1
            continue
        cmd = data[i + 1]
        if cmd == 0xFF:  # escaped literal 0xFF
            out.append(0xFF)
            i += 2
        elif cmd in (0xFB, 0xFC, 0xFD, 0xFE):  # WILL/WONT/DO/DONT — 3 bytes
            i += 3
        elif cmd == 0xFA:  # SB — subnegotiation, skip until IAC SE (FF F0)
            i += 2
            while i + 1 < n and not (data[i] == 0xFF and data[i + 1] == 0xF0):
                i += 1
            i += 2
        else:
            i += 2  # other 2-byte IAC commands
    return bytes(out)


def _read_buffered(sock: socket.socket) -> bytes:
    """Read whatever is currently available without blocking. Mirrors telnetlib.read_very_eager."""
    sock.setblocking(False)
    buf = bytearray()
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
    except (BlockingIOError, OSError):
        pass
    finally:
        sock.setblocking(True)
    return bytes(buf)


def _telnet_ping(
    eve_ng_host: str,
    console_port: int,
    dest_ip: str,
    count: int = 3,
) -> tuple[bool, str]:
    """Connect to a host via EVE-NG telnet console and run ping."""
    try:
        sock = socket.create_connection((eve_ng_host, console_port), timeout=10)
        try:
            time.sleep(1)
            sock.sendall(b"\r\n")
            time.sleep(1)
            _read_buffered(sock)
            sock.sendall(f"ping -c {count} -W 2 {dest_ip}\r\n".encode())
            time.sleep(count * 3 + 2)
            raw = _read_buffered(sock)
        finally:
            sock.close()
        output = _strip_iac(raw).decode("utf-8", errors="replace")
        ok = "0% packet loss" in output or f"{count} packets received" in output
        return ok, output.strip()
    except Exception as e:
        return False, f"Telnet error: {type(e).__name__}: {e}"


def _ssh_ping(
    host_name: str,
    host_ip: str,
    dest_ip: str,
    username: str,
    password: str,
    count: int = 3,
) -> tuple[bool, str]:
    """SSH to a Linux host and run ping. Returns (success, raw_output)."""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host_ip,
            username=username,
            password=password,
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        _stdin, stdout, _stderr = client.exec_command(
            f"ping -c {count} -W 2 {dest_ip}",
            timeout=15,
        )
        output = stdout.read().decode()
        client.close()
        ok = "0% packet loss" in output or f"{count} packets received" in output
        return ok, output.strip()
    except Exception as e:
        return False, f"SSH error: {type(e).__name__}: {e}"


def _resolve_host_ip(spec: dict, host_name: str) -> str:
    """Find a host's data-plane IP from its interfaces in the spec."""
    for _sk, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == host_name:
                for iface in dev.get("interfaces", []):
                    if "ipv4" in iface:
                        return iface["ipv4"].split("/")[0]
    return ""


def run_reachability(
    spec: dict,
    dry_run: bool = False,
) -> tuple[int, int, list[dict]]:
    """Run all reachability tests. Returns (pass_count, fail_count, results)."""
    matrix = spec.get("tests", {}).get("reachability_matrix", [])
    if not matrix:
        print("No reachability tests defined in spec.")
        return 0, 0, []

    mgmt_ips = get_mgmt_ips()
    creds = None
    console_ports: dict[str, int] = {}
    eve_ng_host = ""
    if not dry_run:
        creds = require_credentials("device_username", "device_password")
        console_ports = _get_console_ports()
        if console_ports:
            eve_creds = load_credentials()
            eve_ng_host = eve_creds.eve_ng_host

    passed, failed = 0, 0
    results: list[dict] = []
    print(f"Running {len(matrix)} reachability tests:\n")

    for test in matrix:
        src = test["source"]
        dst = test["destination"]
        desc = test["description"]

        src_mgmt = mgmt_ips.get(src, "")
        dst_data_ip = _resolve_host_ip(spec, dst)

        if not dst_data_ip:
            print(f"  {src} -> {dst} ({desc})")
            print("    SKIP (no data IP for destination)")
            results.append(
                {"src": src, "dst": dst, "desc": desc, "status": "SKIP", "detail": "no data IP"}
            )
            failed += 1
            continue

        if dry_run:
            print(f"  {src} -> {dst} ({desc}) — DRY RUN")
            results.append(
                {"src": src, "dst": dst, "desc": desc, "status": "DRY_RUN", "detail": ""}
            )
            passed += 1
            continue

        # Try SSH first, fall back to EVE-NG telnet console
        ok = False
        output = ""
        method = ""
        if src_mgmt:
            print(f"  {src} -> {dst} ({desc})")
            print(f"    SSH to {src} ({src_mgmt}), ping {dst} ({dst_data_ip})", end="")
            ok, output = _ssh_ping(
                src, src_mgmt, dst_data_ip, creds.device_username, creds.device_password
            )
            method = "ssh"

        if not ok and src in console_ports and eve_ng_host:
            port = console_ports[src]
            if method == "ssh":
                print(" — SSH failed, trying console...", end="")
            else:
                print(f"  {src} -> {dst} ({desc})")
                print(f"    Console to {src} (port {port}), ping {dst} ({dst_data_ip})", end="")
            ok, output = _telnet_ping(eve_ng_host, port, dst_data_ip)
            method = "console"

        if not method:
            print(f"  {src} -> {dst} ({desc})")
            print("    SKIP (no mgmt IP and no console port)")
            results.append(
                {"src": src, "dst": dst, "desc": desc, "status": "SKIP", "detail": "unreachable"}
            )
            failed += 1
            continue

        if ok:
            print(f" — PASS ({method})")
            results.append(
                {"src": src, "dst": dst, "desc": desc, "status": "PASS", "detail": output}
            )
            passed += 1
        else:
            print(f" — FAIL ({method})")
            results.append(
                {"src": src, "dst": dst, "desc": desc, "status": "FAIL", "detail": output}
            )
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed out of {len(matrix)} tests")
    return passed, failed, results


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
    _passed, failed, _results = run_reachability(spec, args.dry_run)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
