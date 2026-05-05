"""Importing this package registers every scenario into the global REGISTRY.

Add a new scenario by importing its module here. Each module's import
side-effects are what register the scenario.
"""

from __future__ import annotations

from . import (
    evpn_anycast_gw_mac_mismatch,  # noqa: F401
    evpn_rr_peer_missing,  # noqa: F401
    evpn_vni_mapping_missing,  # noqa: F401
    evpn_wrong_rt_import,  # noqa: F401
    l1_iface_admin_down,  # noqa: F401
    l1_iface_description_swap,  # noqa: F401
    multi_fault_localpref_and_iface,  # noqa: F401
    underlay_mtu_mismatch,  # noqa: F401
    wan_aspath_prepend_wrong_direction,  # noqa: F401
    wan_bgp_timer_mismatch,  # noqa: F401
    wan_localpref_reversed,  # noqa: F401
    wan_md5_auth_mismatch,  # noqa: F401
    wan_prefix_filter_typo,  # noqa: F401
)
