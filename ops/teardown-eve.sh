#!/usr/bin/env bash
# Tear down the NetworkOps-eve K3s deployment.
#
# DESTRUCTIVE: deletes the networkops-eve namespace including all PVCs
# (Postgres, Redis, ChromaDB, SQLite auth DB), Secrets, ConfigMaps, and
# Deployments. Requires interactive confirmation.
#
# Does NOT touch:
#   - The 'networkops' namespace (Containerlab instance)
#   - In-cluster NetBox or its data
#   - The EVE-NG lab itself
#   - NetBox tenants ai-infra-lab / ai-infra-lab-fw
#
# To redeploy after teardown, re-run:
#   ~/PycharmProjects/networkops/k8s/overlays/eve-ng/install.sh
#
# Usage:
#   ops/teardown-eve.sh

set -euo pipefail

NS=networkops-eve

cat <<EOF
=========================================================
  This will DELETE namespace '$NS' including all PVCs.
=========================================================

The following will be removed:
  - All pods + deployments (api, batfish, celery-worker, celery-beat,
    postgres, redis, frontend)
  - All PVCs (Postgres, Redis, api-data with SQLite auth DB + ChromaDB)
  - networkops-secrets (device creds, JWT, Anthropic key, NetBox token)
  - networkops-eve-tls (self-signed cert)
  - All ConfigMaps and CiliumNetworkPolicies in the namespace

The following will NOT be touched:
  - Production 'networkops' namespace (Containerlab dashboard)
  - In-cluster NetBox and its data
  - EVE-NG lab nodes
  - NetBox tenants and devices (ai-infra-lab, ai-infra-lab-fw)

EOF

read -r -p "Type the namespace name '$NS' to confirm: " ANSWER
if [[ "$ANSWER" != "$NS" ]]; then
    echo "Aborted."
    exit 1
fi

echo "==> deleting namespace $NS (waiting for finalizers)"
kubectl delete namespace "$NS" --wait=true

echo "==> done. To redeploy: ~/PycharmProjects/networkops/k8s/overlays/eve-ng/install.sh"
