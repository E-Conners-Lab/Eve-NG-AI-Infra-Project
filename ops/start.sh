#!/usr/bin/env bash
# Start the EVE-NG lab and refresh NetworkOps-eve dashboard state.
#
# Sequence:
#   1. Boot all lab nodes via EVE-NG REST API (scripts.eve_client)
#   2. Wait for management interfaces to become SSH-reachable (testbed.yaml IPs)
#   3. Run `make refresh-netbox` to sync NetBox + roll the eve dashboard
#   4. Print status summary
#
# Reads EVE-NG creds from .env (project convention via scripts.credentials).
# Override the lab path with EVE_NG_LAB_PATH (default: "/AI Infra Lab.unl").
#
# Usage:
#   ops/start.sh                              # full cold start
#   EVE_NG_LAB_PATH=/MyLab.unl ops/start.sh   # alternate lab

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1. Boot lab via EVE-NG REST API
echo "==> booting lab via EVE-NG REST API"
python3 - <<'PY'
import os
from scripts.eve_client import EveNgClient
from scripts.credentials import require_credentials

creds = require_credentials("eve_ng_host", "eve_ng_password")
lab_path = os.environ.get("EVE_NG_LAB_PATH", "/AI Infra Lab.unl")

client = EveNgClient(creds.eve_ng_host, creds.eve_ng_username, creds.eve_ng_password)
client.login()
try:
    print(f"  lab_path={lab_path}")
    r = client.start_all_nodes(lab_path)
    print(f"  EVE-NG response: {r.get('status', r)}")
finally:
    client.logout()
PY

# 2. Wait for SSH on each testbed mgmt IP (max 5 minutes)
echo "==> waiting for lab nodes to become SSH-reachable (max 5min)"
python3 - <<'PY'
import socket
import sys
import time

import yaml

testbed = yaml.safe_load(open("agent/testbed.yaml").read())
ips = []
for name, dev in (testbed.get("devices") or {}).items():
    ip = (dev.get("connections") or {}).get("cli", {}).get("ip")
    if ip:
        ips.append((name, ip))

deadline = time.time() + 300
pending = set(ips)
ready = set()
while pending and time.time() < deadline:
    next_pending = set()
    for name, ip in pending:
        try:
            with socket.create_connection((ip, 22), timeout=2):
                if name not in ready:
                    print(f"  ready: {name} ({ip})")
                    ready.add(name)
        except Exception:
            next_pending.add((name, ip))
    pending = next_pending
    if pending:
        time.sleep(10)
        sample = sorted(n for n, _ in pending)[:3]
        print(f"  ... {len(pending)} pending: {sample}{'...' if len(pending) > 3 else ''}")

if pending:
    unreached = sorted(n for n, _ in pending)
    print(f"WARN: {len(unreached)} devices unreachable after 5min: {unreached}", file=sys.stderr)
    sys.exit(1)
print(f"  all {len(ready)} devices SSH-reachable")
PY

# 3. Refresh NetBox + roll dashboard
echo "==> refreshing NetBox and rolling networkops-eve api"
make refresh-netbox

# 4. Status summary
echo
"$REPO_ROOT/ops/status.sh"
