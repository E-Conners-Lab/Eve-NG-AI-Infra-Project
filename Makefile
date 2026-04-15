.PHONY: test lint validate generate-spec generate-configs install dev-install clean

# Default target
all: lint validate test

# Install production dependencies
install:
	uv pip install -e .

# Install dev dependencies
dev-install:
	uv pip install -e ".[dev]"

# Run all tests
test:
	pytest tests/ -v

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

# Generate YAML spec from NetBox (requires NETBOX_URL and NETBOX_TOKEN env vars)
generate-spec:
ifndef NETBOX_URL
	$(error NETBOX_URL is not set. Export it before running: export NETBOX_URL=http://localhost:8000)
endif
ifndef NETBOX_TOKEN
	$(error NETBOX_TOKEN is not set. Export it before running: export NETBOX_TOKEN=<your-v2-token>)
endif
	python generator/netbox_to_spec.py

# Render per-device configs from spec
generate-configs:
	@echo "Config generation not yet implemented"

# Remove generated artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf configs/generated/*.cfg
