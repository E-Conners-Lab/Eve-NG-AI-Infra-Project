"""Network automation toolkit — Nornir runbooks + pyATS-based testing.

Layered architecture:
- inventory.py    — pyATS testbed -> Nornir hosts (single source of truth)
- bgp_state.py    — vendor-agnostic BGP summary parser (Genie + ntc-templates)
- runbooks/       — Nornir parallel-execution scripts
"""
