"""Shared bootstrap configuration loader.

Single source for all lab bootstrap data — management IPs, EVE-NG platform
mappings, layout positions. Both create_topology.py and populate_netbox_contexts.py
import from here. Change data in configs/lab_bootstrap.yaml, not in code.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BOOTSTRAP_PATH = Path(__file__).parent.parent / "configs" / "lab_bootstrap.yaml"


def _load() -> dict:
    """Load and cache the bootstrap config."""
    return yaml.safe_load(BOOTSTRAP_PATH.read_text())


def get_mgmt_ips() -> dict[str, str]:
    """Return device-name → management-IP mapping."""
    return _load()["management"]["devices"]


def get_platform_map() -> dict[str, dict]:
    """Return platform → EVE-NG template/image/resource mapping."""
    return _load()["eve_ng_platforms"]


def get_mgmt_labels() -> dict[str, str]:
    """Return platform → EVE-NG management interface label."""
    return _load()["eve_ng_mgmt_labels"]


def get_layout() -> dict[str, list[int]]:
    """Return device-name → [left, top] canvas positions."""
    return _load()["eve_ng_layout"]


def get_shared_overlay_asn() -> int:
    """Return the shared overlay ASN for cross-site EVPN."""
    return _load()["shared_overlay_asn"]


def get_mgmt_gateway() -> str:
    """Return the management network gateway IP."""
    return _load()["management"]["gateway"]
