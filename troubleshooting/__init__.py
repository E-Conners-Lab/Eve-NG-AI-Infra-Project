"""Troubleshooting scenarios for the EVE-NG lab.

Each scenario is a curated, named fault that you inject from your laptop and
then troubleshoot from inside the EVE device consoles. The framework provides:

  - A registry of named scenarios (`troubleshooting.scenarios`).
  - A CLI runner: `python -m troubleshooting <list|inject|status|fix|restore|runbook>`.
  - Per-scenario runbooks under `troubleshooting/runbooks/` with the answer key.

The workflow is intentionally adversarial: you don't see *what* was broken
from the CLI — only whether the fault is still present. Open the runbook
when you're ready for the answer.
"""
