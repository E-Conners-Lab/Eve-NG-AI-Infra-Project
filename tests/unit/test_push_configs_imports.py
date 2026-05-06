"""Regression test: scripts.push_configs must import cleanly in a fresh interpreter.

boto3 is a required runtime dep for the IPsec PSK injection path. If a future
PR drops boto3 from runtime deps, the module-level `import boto3` would break
all device pushes — not just IPsec ones — at import time. This test catches
that drift.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def test_push_configs_imports_cleanly() -> None:
    """A fresh subprocess interpreter must import scripts.push_configs without error."""
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.push_configs"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scripts.push_configs failed to import (likely missing runtime dep). "
        f"stderr:\n{result.stderr}"
    )
