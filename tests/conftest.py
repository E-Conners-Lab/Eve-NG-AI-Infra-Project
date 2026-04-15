"""Shared test fixtures for ai-infra-lab."""

from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def schema_path(project_root: Path) -> Path:
    """Return the path to the JSON Schema file."""
    return project_root / "specs" / "schema.json"


@pytest.fixture
def specs_dir(project_root: Path) -> Path:
    """Return the path to the generated specs directory."""
    return project_root / "specs" / "generated"
