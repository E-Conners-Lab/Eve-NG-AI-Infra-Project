.PHONY: test lint validate generate-spec generate-configs validate-configs \
       generate-testbed bootstrap-mgmt push-configs test-reachability deploy \
       build-snapshot validate-batfish chaos-test deploy-safe \
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
validate-batfish: build-snapshot
	python -m batfish.validate

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
