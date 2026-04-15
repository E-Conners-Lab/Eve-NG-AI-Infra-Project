"""Fabric Health Skill.

Validates eBGP underlay, iBGP EVPN overlay, VTEP reachability,
VNI-to-VLAN mappings, and anycast gateway consistency across
the DC-East and DR-West EVPN-VXLAN fabrics.
"""

from __future__ import annotations

import re


def get_managed_devices(spec: dict) -> list[str]:
    """Return the list of managed device names from the spec's agent boundary.

    This is the authoritative list — the agent NEVER contacts devices
    not in this list.
    """
    return list(spec.get("agent", {}).get("boundary", {}).get("managed", []))


def parse_bgp_summary(raw_output: str, platform: str) -> dict:
    """Parse BGP summary output into structured data.

    Works for both Arista EOS and Cisco IOS-XE BGP summary formats.

    Returns:
        {
            "total_neighbors": int,
            "established": int,
            "down": int,
            "healthy": bool,
            "neighbors": [{"neighbor": str, "asn": int, "state": str, "prefixes_received": int}],
            "down_neighbors": [{"neighbor": str, "state": str}],
        }
    """
    neighbors: list[dict] = []
    down_neighbors: list[dict] = []

    # Both EOS and IOS-XE have similar tabular BGP summary format
    # Match lines that start with an IP address
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue

        # Check if line starts with an IP address (neighbor line)
        if not re.match(r"\d+\.\d+\.\d+\.\d+", line):
            continue

        # Split by whitespace — works for both EOS and IOS-XE formats
        # EOS:    IP  V  AS  MsgRcvd  MsgSent  InQ  OutQ  Up/Down  State/PfxRcd  [PfxAcc]
        # IOS-XE: IP  V  AS  MsgRcvd  MsgSent  TblVer  InQ  OutQ  Up/Down  State/PfxRcd
        parts = line.split()
        if len(parts) < 9:
            continue

        neighbor_ip = parts[0]
        asn = int(parts[2])

        # Determine BGP state. A non-established session has a state name
        # (Active, Idle, Connect, etc.) somewhere in the fields.
        # An established session has only numbers after Up/Down time.
        bgp_down_states = {"Active", "Idle", "Connect", "OpenSent", "OpenConfirm"}
        state = "Established"
        pfx_count = 0

        for field in parts[3:]:  # Skip IP, version, ASN
            if field in bgp_down_states:
                state = field
                break

        if state == "Established":
            # Last field (or second-to-last for EOS with PfxAcc) is prefix count
            try:
                # EOS: second-to-last is PfxRcd, last is PfxAcc
                # IOS-XE: last is PfxRcd
                pfx_count = int(parts[-1])
            except ValueError:
                state = parts[-1]
                pfx_count = 0

        # "Estab" is EOS shorthand for Established
        if state.startswith("Estab"):
            state = "Established"

        entry = {
            "neighbor": neighbor_ip,
            "asn": asn,
            "state": state,
            "prefixes_received": pfx_count,
        }
        neighbors.append(entry)

        if state != "Established":
            down_neighbors.append({"neighbor": neighbor_ip, "state": state})

    established = sum(1 for n in neighbors if n["state"] == "Established")

    return {
        "total_neighbors": len(neighbors),
        "established": established,
        "down": len(neighbors) - established,
        "healthy": established == len(neighbors) and len(neighbors) > 0,
        "neighbors": neighbors,
        "down_neighbors": down_neighbors,
    }
