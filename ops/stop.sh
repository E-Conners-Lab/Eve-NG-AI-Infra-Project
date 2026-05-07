#!/usr/bin/env bash
# Stop the EVE-NG lab gracefully.
#
# Halts every node in the lab via the EVE-NG REST API. Does NOT touch
# K3s — NetworkOps-eve persists across lab cycles. To fully tear down
# NetworkOps-eve too, see ops/teardown-eve.sh.
#
# Usage:
#   ops/stop.sh
#   EVE_NG_LAB_PATH=/MyLab.unl ops/stop.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> stopping lab via EVE-NG REST API"
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
    r = client.stop_all_nodes(lab_path)
    print(f"  EVE-NG response: {r.get('status', r)}")
finally:
    client.logout()
PY

echo "==> done. NetworkOps-eve dashboard remains running; devices will show grey until lab restarts."
