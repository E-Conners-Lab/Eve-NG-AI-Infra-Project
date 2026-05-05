"""Enables `python -m troubleshooting <subcommand>`."""

from __future__ import annotations

import sys

from troubleshooting.cli import main

sys.exit(main())
