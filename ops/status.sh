#!/usr/bin/env bash
# Show health/status across the project: EVE-NG lab nodes, NetBox tenant
# device counts, and NetworkOps-eve K3s deployment.
#
# Read-only; safe to run anytime. Each section is independent — failures
# in one don't block the others.
#
# Usage:
#   ops/status.sh

# Note: no `set -e` — we want to continue past per-section failures.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=========================================="
echo "  EVE-NG lab nodes"
echo "=========================================="
python3 - <<'PY' || echo "  (EVE-NG status query failed — host unreachable or creds missing?)"
import os
from scripts.eve_client import EveNgClient
from scripts.credentials import require_credentials

creds = require_credentials("eve_ng_host", "eve_ng_password")
lab_path = os.environ.get("EVE_NG_LAB_PATH", "/AI Infra Lab.unl")

client = EveNgClient(creds.eve_ng_host, creds.eve_ng_username, creds.eve_ng_password)
client.login()
try:
    r = client.get_nodes(lab_path)
    nodes = (r or {}).get("data") or {}
    # EVE-NG status codes: 0=stopped, 1=starting?, 2=started, 3=running
    running = sum(1 for n in nodes.values() if str(n.get("status")) in ("2", "3"))
    print(f"  lab_path={lab_path}  total={len(nodes)}  running={running}")
    for _nid, n in sorted(nodes.items(), key=lambda x: x[1].get("name", "")):
        st = n.get("status")
        sym = "[ON]" if str(st) in ("2", "3") else "[--]"
        print(f"  {sym}  {n.get('name', '?'):20s} status={st}")
finally:
    client.logout()
PY

echo
echo "=========================================="
echo "  NetBox tenant device counts (in-cluster)"
echo "=========================================="
TOKEN=$(kubectl -n networkops get secret networkops-secrets -o jsonpath='{.data.netbox-api-token}' 2>/dev/null | base64 -d)
if [ -z "$TOKEN" ]; then
    echo "  (no NetBox token — is the K3s cluster reachable?)"
else
    kubectl -n netbox port-forward svc/netbox 18080:8080 >/dev/null 2>&1 &
    PF=$!
    trap 'kill $PF 2>/dev/null || true' EXIT
    sleep 2
    for slug in ai-infra-lab ai-infra-lab-fw; do
        count=$(curl -sS -H "Authorization: Token $TOKEN" "http://localhost:18080/api/dcim/devices/?tenant=$slug" 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin).get('count','?'))" 2>/dev/null)
        echo "  $slug: $count devices"
    done
    kill $PF 2>/dev/null || true
    trap - EXIT
fi

echo
echo "=========================================="
echo "  NetworkOps-eve K3s pods"
echo "=========================================="
kubectl -n networkops-eve get pods --no-headers 2>/dev/null | awk '{printf "  %-40s %-10s %s restarts=%s\n", $1, $3, $2, $4}' \
    || echo "  (cluster unreachable)"

echo
echo "=========================================="
echo "  Dashboard reachability"
echo "=========================================="
curl -sk --max-time 3 -o /dev/null -w "  https://networkops-eve.local: http=%{http_code} time=%{time_total}s\n" \
    https://networkops-eve.local 2>/dev/null \
    || echo "  (unreachable — check /etc/hosts: 192.168.68.200 networkops-eve.local)"
