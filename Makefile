.PHONY: test lint validate generate-spec generate-configs validate-configs install dev-install clean

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

# Generate YAML spec from NetBox (reads credentials from .env)
generate-spec:
	python -m generator.netbox_to_spec

# Render per-device configs from spec
generate-configs:
	python generator/render_configs.py

# Validate rendered configs (run config generation tests)
validate-configs:
	pytest tests/unit/test_config_gen.py -v

# Remove generated artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf configs/generated/*.cfg
