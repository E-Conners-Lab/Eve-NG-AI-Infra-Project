.PHONY: test lint validate generate-spec generate-configs validate-configs \
       generate-testbed bootstrap-mgmt push-configs test-reachability deploy \
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
	python generator/render_configs.py

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

# Remove generated artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf configs/generated/*.cfg logs/*.jsonl
