"""Vendor-agnostic BGP summary parsing via ntc-templates.

ntc-templates has both `cisco_ios show ip bgp summary` and `arista_eos show ip
bgp summary` parsers, but the field names differ between vendors. This module
normalizes them to a single schema so the rest of the codebase doesn't care.

Normalized schema:
    {
        "router_id": "10.1.0.1",
        "local_as": 65000,
        "neighbors": [
            {
                "neighbor": "10.1.1.1",
                "remote_as": 65001,
                "state": "Established" | "Active" | "Idle" | ...,
                "prefixes_received": 9 | None,
                "uptime": "04:26:05" | None,
            },
            ...
        ],
    }
"""

from __future__ import annotations

from typing import Any

from ntc_templates.parse import parse_output

# pyATS os name -> ntc-templates platform name
_OS_TO_NTC = {
    "eos": "arista_eos",
    "iosxe": "cisco_ios",  # ntc-templates packages cisco_xe under cisco_ios
    "ios": "cisco_ios",
}

# Per-platform field-name maps -> normalized keys.
# ntc-templates field names differ between vendor templates; we collapse them.
_FIELD_MAPS = {
    "arista_eos": {
        "neighbor": "bgp_neigh",
        "remote_as": "neigh_as",
        "state": "state",
        "prefixes_received": "state_pfxrcd",
        "uptime": "up_down",
    },
    "cisco_ios": {
        "neighbor": "bgp_neighbor",
        "remote_as": "neighbor_as",
        "state": "state_or_prefixes_received",
        "prefixes_received": "state_or_prefixes_received",
        "uptime": "up_down",
    },
}


def parse_bgp_summary(os_name: str, raw: str) -> dict:
    """Parse `show ip bgp summary` raw output to the normalized schema."""
    ntc_platform = _OS_TO_NTC.get(os_name)
    if not ntc_platform:
        raise ValueError(f"unsupported os for BGP summary parsing: {os_name}")

    rows = parse_output(platform=ntc_platform, command="show ip bgp summary", data=raw or "")
    if not rows:
        return {"router_id": "", "local_as": None, "neighbors": []}

    fmap = _FIELD_MAPS[ntc_platform]
    out: dict[str, Any] = {
        "router_id": rows[0].get("router_id", ""),
        "local_as": _safe_int(rows[0].get("local_as")),
        "neighbors": [],
    }
    for r in rows:
        if ntc_platform == "arista_eos":
            # EOS has separate state and prefix-count columns
            state = _normalize_state(r.get("state", ""))
            pfx_recv = _safe_int(r.get("state_pfxrcd"))
        else:
            # IOS-XE / IOS overload the same column with state-or-prefix-count
            raw_state = r.get(fmap["state"], "")
            try:
                pfx_recv = int(raw_state)
                state = "Established"
            except (ValueError, TypeError):
                pfx_recv = None
                state = _normalize_state(raw_state)

        out["neighbors"].append(
            {
                "neighbor": r.get(fmap["neighbor"], ""),
                "remote_as": _safe_int(r.get(fmap["remote_as"])),
                "state": state,
                "prefixes_received": pfx_recv,
                "uptime": r.get(fmap["uptime"], ""),
                "address_family": "ipv4 unicast",
            }
        )
    return out


def _normalize_state(s: str) -> str:
    """Collapse vendor short forms to canonical names."""
    return {
        "Estab": "Established",
        "ESTAB": "Established",
        "Idle": "Idle",
        "Active": "Active",
        "Connect": "Connect",
    }.get(s, s)


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None
