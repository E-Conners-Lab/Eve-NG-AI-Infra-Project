"""Branch Connectivity Skill.

Validates eBGP sessions on br-ce-1 to both PE routers, confirms route
advertisement (branch prefixes toward WAN, DC/DR prefixes from WAN),
and tests reachability from branch to DC and DR prefixes.
"""

from __future__ import annotations

from agent.skills.fabric_health.skill import parse_bgp_summary

__all__ = ["parse_bgp_summary"]
