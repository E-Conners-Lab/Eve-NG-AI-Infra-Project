.PHONY: test lint validate generate-spec generate-configs validate-configs \
       generate-testbed bootstrap-mgmt push-configs test-reachability deploy \
       build-snapshot validate-batfish chaos-test deploy-safe \
       sync-aws-outputs seed-cloud-aws refresh-netbox \
       start stop status teardown-eve \
       install dev-install clean

# Default target
all: lint validate test

# Install production dependencies
install:
	uv pip install -e .

# Install dev dependencies
dev-install:
	uv pip install -e ".[dev]"

# Run all unit tests (excludes integration tests requiring live devices)
test:
	pytest tests/ -v --ignore=tests/integration/

# Run integration tests (requires live EVE-NG devices)
test-integration:
	pytest tests/integration/ -v

# Run linting
lint:
	ruff check .
	ruff format --check .

# Auto-fix lint issues
lint-fix:
	ruff check --fix .
	ruff format .

# Validate generated specs against JSON Schema
validate:
	pytest tests/unit/test_schema.py::TestGeneratedSpecFile -v

# Generate YAML spec from NetBox (reads credentials from .env)
generate-spec:
	python -m generator.netbox_to_spec

# Render per-device configs from spec
generate-configs:
	python -m generator.render_configs

# Validate rendered configs (run config generation tests)
validate-configs:
	pytest tests/unit/test_config_gen.py -v

# Generate pyATS testbed from spec
generate-testbed:
	python -m scripts.generate_testbed

# Bootstrap management IPs via EVE-NG API (first-time setup, no SSH needed)
bootstrap-mgmt:
	python -m scripts.bootstrap_mgmt

# Push configs to EVE-NG devices via SSH (requires .env credentials + mgmt IPs)
push-configs:
	python -m scripts.push_configs

# Run reachability test matrix (requires live devices with configs applied)
test-reachability:
	python -m scripts.run_reachability

# Full deployment cycle: generate → push → verify
deploy: generate-spec generate-configs generate-testbed push-configs test-reachability

# Run the agent (all skills once)
agent:
	python -m agent.runner

# Run a specific skill
agent-fabric:
	python -m agent.runner --skill fabric_health

agent-branch:
	python -m agent.runner --skill branch_connectivity

agent-compliance:
	python -m agent.runner --skill spec_compliance

# Run agent on a schedule (every 5 minutes)
agent-monitor:
	python -m agent.runner --schedule 300

# Build Batfish snapshot from spec + generated configs
build-snapshot:
	python -m batfish.snapshot

# Batfish pre-deploy validation (requires Batfish Docker container running)
#
# IPsec gap: Batfish does NOT model Cisco IKEv2/IPsec crypto blocks. The IPsec
# section in dc-ce-1.cfg is effectively skipped during validation. Ground truth
# for tunnel health comes from the MCP cloud_tunnel_health tool, not Batfish.
validate-batfish: build-snapshot
	python -m batfish.validate

# Pull AWS Terraform outputs into .aws_outputs.json for the cloud-aws seed step.
# Requires AWS_REPO env var pointing at the cloned cloud-devops-pipeline repo.
sync-aws-outputs:
	@if [ -z "$$AWS_REPO" ]; then \
		echo "ERROR: AWS_REPO is not set. Point it at the cloud-devops-pipeline repo path." >&2; \
		exit 1; \
	fi
	terraform -chdir=$$AWS_REPO/terraform/environments/dev output -json > .aws_outputs.json
	@echo "Wrote $$(pwd)/.aws_outputs.json"

# Seed NetBox with the cloud-aws site + IPsec tunnel data on dc-ce-1.
# Run after sync-aws-outputs. Idempotent.
seed-cloud-aws:
	python -m scripts.populate_cloud_aws

# Refresh NetBox from spec + testbed and roll the NetworkOps-eve dashboard.
# Use this after editing the lab (new device, IP change, topology rebuild).
# Requires:
#   - kubectl pointing at the k3s cluster (in-cluster netbox + networkops-eve)
#   - specs/generated/lab_spec.yaml present (run `make generate-spec` first)
# Port-forwards netbox locally, overrides .env's NETBOX_URL/TOKEN with the
# in-cluster values, runs the three populator/sync scripts, then rolls the
# eve dashboard so it picks up the new inventory immediately.
refresh-netbox:
	@set -e; \
	echo "==> port-forwarding netbox.netbox:8080 to localhost:18080"; \
	kubectl -n netbox port-forward svc/netbox 18080:8080 >/dev/null 2>&1 & \
	PF_PID=$$!; \
	trap "kill $$PF_PID 2>/dev/null" EXIT INT TERM; \
	sleep 3; \
	export NETBOX_URL=http://localhost:18080; \
	export NETBOX_TOKEN=$$(kubectl -n networkops get secret networkops-secrets -o jsonpath='{.data.netbox-api-token}' | base64 -d); \
	if [ -z "$$NETBOX_TOKEN" ]; then \
		echo "ERROR: could not read netbox-api-token from networkops/networkops-secrets" >&2; \
		exit 1; \
	fi; \
	echo "==> populate_netbox (devices, cables, prefixes from spec)"; \
	python -m scripts.populate_netbox; \
	echo "==> populate_netbox_enterprise (tenancy + FortiGate split)"; \
	python -m scripts.populate_netbox_enterprise; \
	echo "==> sync_netbox_mgmt_ips (mgmt IPs from testbed.yaml)"; \
	python -m scripts.sync_netbox_mgmt_ips; \
	echo "==> rolling networkops-eve api"; \
	kubectl -n networkops-eve rollout restart deployment/api; \
	echo "==> done"

# Lab lifecycle — orchestrated startup/teardown. See ops/ for implementation.
# `start` boots the lab via EVE-NG REST API, waits for SSH, refreshes NetBox,
# and rolls the dashboard. `stop` halts the lab. `status` shows current state
# across the EVE-NG host, NetBox, and the K3s deployment. `teardown-eve` is
# the nuclear option for the K3s deployment (interactive confirmation).
start:
	@./ops/start.sh

stop:
	@./ops/stop.sh

status:
	@./ops/status.sh

teardown-eve:
	@./ops/teardown-eve.sh

# Chaos testing — inject faults, verify agent detection, rollback (requires live devices)
chaos-test:
	python -m scripts.chaos_test

# Full deploy with Batfish gate: generate → validate → push → verify
deploy-safe: generate-spec generate-configs validate-batfish push-configs test-reachability agent

# Remove generated artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf configs/generated/*.cfg logs/*.jsonl
