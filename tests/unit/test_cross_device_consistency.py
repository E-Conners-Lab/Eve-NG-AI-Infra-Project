"""Cross-device semantic consistency tests.

Anti-hallucination guardrails: these tests catch the class of bugs where
AI-generated configs are syntactically valid but semantically wrong.
Inspired by Hildenbrand's CDN benchmark findings — an AI agent generated
Junos hash-key config that looked expert but was functionally backward.

These tests verify BOTH sides of every relationship:
- If device A says "neighbor X remote-as Y", device B must say "neighbor Z remote-as W"
  with the reciprocal values.
- VNI/VLAN mappings must be consistent across all leaves in a fabric.
- /31 links must have correct IP pairs on both ends.
- EVPN address-family must only appear on iBGP overlay sessions.

Reference: https://www.linkedin.com/pulse/intent-over-cli-how-ai-built-multi-vendor-cdn-exposed-hildenbrand-dbtwe/
"""

import re
from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"


@pytest.fixture
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text())


def _render(spec: dict, device_name: str) -> str:
    from generator.render_configs import render_device_config

    return render_device_config(spec, device_name)


def _parse_bgp_neighbors(config: str) -> list[dict]:
    """Parse BGP neighbor statements from a rendered config.

    Returns list of dicts: {ip, remote_as, has_update_source_lo, in_evpn_af, in_ipv4_af}
    """
    neighbors: dict[str, dict] = {}

    # Extract neighbor IP + remote-as pairs
    for match in re.finditer(r"neighbor\s+(\d+\.\d+\.\d+\.\d+)\s+remote-as\s+(\d+)", config):
        ip, remote_as = match.group(1), int(match.group(2))
        if ip not in neighbors:
            neighbors[ip] = {
                "ip": ip,
                "remote_as": remote_as,
                "has_update_source_lo": False,
                "in_evpn_af": False,
                "in_ipv4_af": False,
            }

    # Check for update-source Loopback0
    for match in re.finditer(
        r"neighbor\s+(\d+\.\d+\.\d+\.\d+)\s+update-source\s+Loopback0", config
    ):
        ip = match.group(1)
        if ip in neighbors:
            neighbors[ip]["has_update_source_lo"] = True

    # Parse address-family blocks to determine which neighbors are in which AF
    # Split config into sections by address-family
    evpn_block = re.search(
        r"address-family evpn(.*?)(?:address-family|!\s*$|^!$|\nend)",
        config,
        re.DOTALL | re.MULTILINE,
    )
    if evpn_block:
        for match in re.finditer(
            r"neighbor\s+(\d+\.\d+\.\d+\.\d+)\s+activate", evpn_block.group(1)
        ):
            ip = match.group(1)
            if ip in neighbors:
                neighbors[ip]["in_evpn_af"] = True

    ipv4_block = re.search(
        r"address-family ipv4(.*?)(?:address-family|!\s*$|^!$|\nend)",
        config,
        re.DOTALL | re.MULTILINE,
    )
    if ipv4_block:
        for match in re.finditer(
            r"neighbor\s+(\d+\.\d+\.\d+\.\d+)\s+activate", ipv4_block.group(1)
        ):
            ip = match.group(1)
            if ip in neighbors:
                neighbors[ip]["in_ipv4_af"] = True

    return list(neighbors.values())


def _get_device_asn(spec: dict, device_name: str) -> int | None:
    """Get a device's ASN from the spec."""
    for _key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name and "asn" in dev:
                return dev["asn"]
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] == device_name and "asn" in dev:
            return dev["asn"]
    return None


def _get_device_loopback(spec: dict, device_name: str) -> str | None:
    """Get a device's loopback0 IP (without mask) from the spec."""
    for _key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name and "loopback0" in dev:
                return dev["loopback0"].split("/")[0]
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] == device_name and "loopback0" in dev:
            return dev["loopback0"].split("/")[0]
    return None


# ---------------------------------------------------------------------------
# 1. Cross-Device BGP Peering Consistency
# ---------------------------------------------------------------------------
class TestBGPPeeringReciprocity:
    """If device A peers with device B, B must peer back with A using correct ASNs.

    This catches the Hildenbrand failure mode: syntactically valid BGP config
    that references the wrong ASN or wrong peer IP on one side.
    """

    # DC East underlay: each (spine_iface_ip, leaf_iface_ip, spine_asn, leaf_asn)
    DC_UNDERLAY_PEERS = [
        # spine-1 to leaves/borders
        ("dc-spine-1", "10.1.1.0", "dc-leaf-1", "10.1.1.1", 65000, 65001),
        ("dc-spine-1", "10.1.1.2", "dc-leaf-2", "10.1.1.3", 65000, 65002),
        ("dc-spine-1", "10.1.1.4", "dc-border-1", "10.1.1.5", 65000, 65003),
        ("dc-spine-1", "10.1.1.6", "dc-border-2", "10.1.1.7", 65000, 65004),
        # spine-2 to leaves/borders
        ("dc-spine-2", "10.1.1.8", "dc-leaf-1", "10.1.1.9", 65000, 65001),
        ("dc-spine-2", "10.1.1.10", "dc-leaf-2", "10.1.1.11", 65000, 65002),
        ("dc-spine-2", "10.1.1.12", "dc-border-1", "10.1.1.13", 65000, 65003),
        ("dc-spine-2", "10.1.1.14", "dc-border-2", "10.1.1.15", 65000, 65004),
    ]

    @pytest.mark.parametrize(
        "a_name,a_ip,b_name,b_ip,a_asn,b_asn",
        DC_UNDERLAY_PEERS,
        ids=[f"{p[0]}<->{p[2]}" for p in DC_UNDERLAY_PEERS],
    )
    def test_dc_underlay_ebgp_reciprocal(
        self, spec: dict, a_name: str, a_ip: str, b_name: str, b_ip: str, a_asn: int, b_asn: int
    ) -> None:
        """Both sides of a DC underlay eBGP session must reference each other correctly.

        Device A's config must have: neighbor <b_ip> remote-as <b_asn>
        Device B's config must have: neighbor <a_ip> remote-as <a_asn>
        """
        config_a = _render(spec, a_name)
        config_b = _render(spec, b_name)

        # A side: peers with B's IP using B's ASN
        assert f"neighbor {b_ip} remote-as {b_asn}" in config_a, (
            f"{a_name} missing: neighbor {b_ip} remote-as {b_asn}"
        )
        # B side: peers with A's IP using A's ASN
        assert f"neighbor {a_ip} remote-as {a_asn}" in config_b, (
            f"{b_name} missing: neighbor {a_ip} remote-as {a_asn}"
        )

    def test_dc_overlay_ibgp_spine_to_leaf_reciprocal(self, spec: dict) -> None:
        """Spine EVPN overlay peers with leaf loopbacks, and leaves peer back with spine loopbacks.

        iBGP EVPN: same ASN (65000) on both sides, update-source Loopback0.
        """
        leaf_names = ["dc-leaf-1", "dc-leaf-2", "dc-border-1", "dc-border-2"]
        spine_names = ["dc-spine-1", "dc-spine-2"]

        for spine_name in spine_names:
            spine_config = _render(spec, spine_name)
            spine_lo = _get_device_loopback(spec, spine_name)

            for leaf_name in leaf_names:
                leaf_lo = _get_device_loopback(spec, leaf_name)
                leaf_config = _render(spec, leaf_name)

                # Spine must peer with leaf's loopback
                assert f"neighbor {leaf_lo} remote-as 65000" in spine_config, (
                    f"{spine_name} missing iBGP EVPN to {leaf_name} ({leaf_lo})"
                )
                # Leaf must peer with spine's loopback
                assert f"neighbor {spine_lo} remote-as 65000" in leaf_config, (
                    f"{leaf_name} missing iBGP EVPN to {spine_name} ({spine_lo})"
                )

    def test_dr_collapsed_ebgp_reciprocal(self, spec: dict) -> None:
        """DR leaves must have reciprocal eBGP underlay peering."""
        config_1 = _render(spec, "dr-leaf-1")
        config_2 = _render(spec, "dr-leaf-2")

        # dr-leaf-1 (10.31.1.0) peers with dr-leaf-2 (10.31.1.1)
        assert "neighbor 10.31.1.1 remote-as 65202" in config_1, (
            "dr-leaf-1 missing eBGP to dr-leaf-2"
        )
        assert "neighbor 10.31.1.0 remote-as 65201" in config_2, (
            "dr-leaf-2 missing eBGP to dr-leaf-1"
        )

    def test_dr_collapsed_ibgp_evpn_reciprocal(self, spec: dict) -> None:
        """DR collapsed design: both leaves must peer iBGP EVPN with each other's loopbacks.

        This is the collapsed spine-leaf design — no separate spines, so the two
        leaves must be direct iBGP EVPN peers for VXLAN to work.
        """
        config_1 = _render(spec, "dr-leaf-1")
        config_2 = _render(spec, "dr-leaf-2")
        lo_1 = _get_device_loopback(spec, "dr-leaf-1")  # 10.31.0.1
        lo_2 = _get_device_loopback(spec, "dr-leaf-2")  # 10.31.0.2

        # dr-leaf-1 must have iBGP EVPN neighbor pointing to dr-leaf-2's loopback
        assert f"neighbor {lo_2}" in config_1, (
            f"dr-leaf-1 missing iBGP EVPN neighbor {lo_2} (dr-leaf-2 loopback) — "
            "collapsed design requires direct EVPN peering between leaves"
        )
        # dr-leaf-2 must have iBGP EVPN neighbor pointing to dr-leaf-1's loopback
        assert f"neighbor {lo_1}" in config_2, (
            f"dr-leaf-2 missing iBGP EVPN neighbor {lo_1} (dr-leaf-1 loopback) — "
            "collapsed design requires direct EVPN peering between leaves"
        )


# ---------------------------------------------------------------------------
# 2. Semantic BGP Address-Family Assertions
# ---------------------------------------------------------------------------
class TestBGPAddressFamilySemantic:
    """EVPN must only appear on iBGP overlay sessions, never on eBGP underlay.

    Hildenbrand lesson: AI agents deploy vendor-correct syntax in the wrong
    context. EVPN on an eBGP underlay session is syntactically valid EOS but
    semantically wrong — it would try to exchange EVPN routes over P2P links
    instead of the loopback-based overlay mesh.
    """

    def test_spine_evpn_only_on_ibgp(self, spec: dict) -> None:
        """dc-spine-1: EVPN neighbors must all be iBGP (same ASN, loopback-sourced)."""
        config = _render(spec, "dc-spine-1")
        neighbors = _parse_bgp_neighbors(config)

        for n in neighbors:
            if n["in_evpn_af"]:
                assert n["remote_as"] == 65000, (
                    f"EVPN activated on non-iBGP neighbor {n['ip']} "
                    f"(remote-as {n['remote_as']}, expected 65000)"
                )
                assert n["has_update_source_lo"], (
                    f"EVPN neighbor {n['ip']} missing update-source Loopback0 — "
                    "overlay sessions must use stable loopback endpoints"
                )

    def test_leaf_evpn_only_on_ibgp(self, spec: dict) -> None:
        """dc-leaf-1: EVPN neighbors must be iBGP to spines, not eBGP underlay."""
        config = _render(spec, "dc-leaf-1")
        neighbors = _parse_bgp_neighbors(config)

        for n in neighbors:
            if n["in_evpn_af"]:
                assert n["remote_as"] == 65000, (
                    f"dc-leaf-1: EVPN on non-iBGP neighbor {n['ip']} (remote-as {n['remote_as']})"
                )
                assert n["has_update_source_lo"], (
                    f"dc-leaf-1: EVPN neighbor {n['ip']} not loopback-sourced"
                )

    def test_non_loopback_sourced_neighbors_not_in_evpn(self, spec: dict) -> None:
        """EVPN must only be activated on loopback-sourced (overlay) sessions.

        The discriminator is update-source Loopback0, NOT ASN. This fabric uses
        eBGP EVPN (leaf AS != spine AS), which is valid — but EVPN must still be
        on loopback-sourced sessions, never on P2P link-addressed sessions.
        A P2P-sourced EVPN session would use an unstable endpoint that breaks
        when the physical link bounces.
        """
        for device_name in ["dc-spine-1", "dc-spine-2", "dc-leaf-1", "dc-leaf-2"]:
            config = _render(spec, device_name)
            neighbors = _parse_bgp_neighbors(config)

            for n in neighbors:
                if n["in_evpn_af"] and not n["has_update_source_lo"]:
                    pytest.fail(
                        f"{device_name}: EVPN activated on neighbor {n['ip']} "
                        f"which is NOT loopback-sourced — EVPN sessions must use "
                        "update-source Loopback0 for stable overlay endpoints"
                    )

    def test_spine_route_reflector_only_in_evpn(self, spec: dict) -> None:
        """route-reflector-client must only appear inside address-family evpn block."""
        for spine in ["dc-spine-1", "dc-spine-2"]:
            config = _render(spec, spine)
            ipv4_block = re.search(
                r"address-family ipv4(.*?)(?:address-family|!\s*$)",
                config,
                re.DOTALL | re.MULTILINE,
            )
            if ipv4_block:
                assert "route-reflector-client" not in ipv4_block.group(1), (
                    f"{spine}: route-reflector-client found in IPv4 address-family — "
                    "RR-client should only be in EVPN AF"
                )


# ---------------------------------------------------------------------------
# 3. VNI/VLAN + Anycast Gateway Consistency
# ---------------------------------------------------------------------------
class TestVNIVLANConsistency:
    """All compute leaves in a fabric must agree on VNI-to-VLAN mappings and gateways.

    If leaf-1 maps VNI 10100 to VLAN 100 but leaf-2 maps VNI 10100 to VLAN 200,
    VXLAN traffic will be silently misdelivered. This is a data-plane corruption
    bug that control-plane checks (BGP session up) will never catch.
    """

    def _extract_vni_vlan_mappings(self, config: str) -> dict[int, int]:
        """Extract {VNI: VLAN} from 'vxlan vni N vlan M' lines."""
        mappings = {}
        for match in re.finditer(r"vxlan\s+vni\s+(\d+)\s+vlan\s+(\d+)", config, re.IGNORECASE):
            mappings[int(match.group(1))] = int(match.group(2))
        return mappings

    def _extract_anycast_gateways(self, config: str) -> dict[int, str]:
        """Extract {VLAN: gateway_ip} from 'interface VlanN / ip address virtual X' blocks."""
        gateways = {}
        for match in re.finditer(
            r"interface\s+Vlan(\d+).*?ip\s+address\s+virtual\s+(\S+)",
            config,
            re.DOTALL,
        ):
            gateways[int(match.group(1))] = match.group(2)
        return gateways

    def _extract_virtual_mac(self, config: str) -> str | None:
        """Extract virtual-router mac-address value."""
        match = re.search(r"ip\s+virtual-router\s+mac-address\s+(\S+)", config)
        return match.group(1) if match else None

    def test_dc_leaves_same_vni_vlan_mappings(self, spec: dict) -> None:
        """dc-leaf-1 and dc-leaf-2 must have identical VNI-to-VLAN mappings."""
        cfg_1 = _render(spec, "dc-leaf-1")
        cfg_2 = _render(spec, "dc-leaf-2")

        map_1 = self._extract_vni_vlan_mappings(cfg_1)
        map_2 = self._extract_vni_vlan_mappings(cfg_2)

        assert map_1 == map_2, (
            f"VNI-VLAN mismatch between dc-leaf-1 and dc-leaf-2:\n"
            f"  dc-leaf-1: {map_1}\n"
            f"  dc-leaf-2: {map_2}"
        )
        # Both must have the expected mappings
        assert 10100 in map_1 and map_1[10100] == 100
        assert 10200 in map_1 and map_1[10200] == 200

    def test_dc_leaves_same_anycast_gateways(self, spec: dict) -> None:
        """All DC compute leaves must have the same anycast gateway IPs per VLAN."""
        cfg_1 = _render(spec, "dc-leaf-1")
        cfg_2 = _render(spec, "dc-leaf-2")

        gw_1 = self._extract_anycast_gateways(cfg_1)
        gw_2 = self._extract_anycast_gateways(cfg_2)

        assert gw_1 == gw_2, (
            f"Anycast gateway mismatch between DC leaves:\n  dc-leaf-1: {gw_1}\n  dc-leaf-2: {gw_2}"
        )
        # VLAN 100 gateway must be 10.10.1.1/24
        assert gw_1.get(100) == "10.10.1.1/24", f"VLAN 100 gateway wrong: {gw_1.get(100)}"
        assert gw_1.get(200) == "10.10.2.1/24", f"VLAN 200 gateway wrong: {gw_1.get(200)}"

    def test_dc_leaves_same_virtual_mac(self, spec: dict) -> None:
        """All DC compute leaves must use the same virtual-router MAC for anycast."""
        cfg_1 = _render(spec, "dc-leaf-1")
        cfg_2 = _render(spec, "dc-leaf-2")

        mac_1 = self._extract_virtual_mac(cfg_1)
        mac_2 = self._extract_virtual_mac(cfg_2)

        assert mac_1 is not None, "dc-leaf-1 missing virtual-router mac-address"
        assert mac_2 is not None, "dc-leaf-2 missing virtual-router mac-address"
        assert mac_1 == mac_2, f"Virtual MAC mismatch: dc-leaf-1={mac_1}, dc-leaf-2={mac_2}"

    def test_dr_leaves_same_vni_vlan_mappings(self, spec: dict) -> None:
        """DR leaves must have identical VNI-to-VLAN mappings."""
        cfg_1 = _render(spec, "dr-leaf-1")
        cfg_2 = _render(spec, "dr-leaf-2")

        map_1 = self._extract_vni_vlan_mappings(cfg_1)
        map_2 = self._extract_vni_vlan_mappings(cfg_2)

        assert map_1 == map_2, (
            f"VNI-VLAN mismatch between DR leaves:\n  dr-leaf-1: {map_1}\n  dr-leaf-2: {map_2}"
        )

    def test_dr_leaves_same_virtual_mac_as_dc(self, spec: dict) -> None:
        """DR leaves must use the same virtual-router MAC as DC leaves for mobility."""
        dc_mac = self._extract_virtual_mac(_render(spec, "dc-leaf-1"))
        dr_mac = self._extract_virtual_mac(_render(spec, "dr-leaf-1"))

        assert dc_mac is not None, "dc-leaf-1 missing virtual-router mac"
        assert dr_mac is not None, "dr-leaf-1 missing virtual-router mac"
        assert dc_mac == dr_mac, (
            f"Virtual MAC mismatch across sites: DC={dc_mac}, DR={dr_mac} — "
            "anycast gateway MAC must be consistent for workload mobility"
        )


# ---------------------------------------------------------------------------
# 4. VTEP Source Uniqueness
# ---------------------------------------------------------------------------
class TestVTEPUniqueness:
    """Each VTEP must have a unique source IP. Duplicate VTEP IPs cause
    VXLAN black-holing — the fabric can't distinguish which leaf originated
    a packet, so return traffic may be sent to the wrong leaf.
    """

    def _extract_vtep_source(self, config: str) -> str | None:
        """Extract the VTEP source IP from Loopback1."""
        match = re.search(r"interface\s+Loopback1.*?ip\s+address\s+(\S+)", config, re.DOTALL)
        return match.group(1) if match else None

    def test_dc_vtep_sources_unique(self, spec: dict) -> None:
        """Each DC compute leaf must have a unique VTEP source loopback IP."""
        vteps = {}
        for name in ["dc-leaf-1", "dc-leaf-2"]:
            vtep = self._extract_vtep_source(_render(spec, name))
            assert vtep is not None, f"{name} missing VTEP source (Loopback1)"
            assert vtep not in vteps.values(), (
                f"VTEP source collision: {name} has {vtep}, "
                f"same as {next((k for k, v in vteps.items() if v == vtep), '?')} — "
                "duplicate VTEP IPs cause VXLAN black-holing"
            )
            vteps[name] = vtep

    def test_dr_vtep_sources_unique(self, spec: dict) -> None:
        """Each DR leaf must have a unique VTEP source loopback IP."""
        vteps = {}
        for name in ["dr-leaf-1", "dr-leaf-2"]:
            vtep = self._extract_vtep_source(_render(spec, name))
            assert vtep is not None, f"{name} missing VTEP source (Loopback1)"
            assert vtep not in vteps.values(), (
                f"VTEP source collision: {name} has {vtep}, "
                f"same as {next((k for k, v in vteps.items() if v == vtep), '?')} — "
                "duplicate VTEP IPs cause VXLAN black-holing"
            )
            vteps[name] = vtep

    def test_vtep_sources_not_overlap_with_loopback0(self, spec: dict) -> None:
        """VTEP source IPs must be in a different range than router-id loopbacks."""
        for name in ["dc-leaf-1", "dc-leaf-2", "dr-leaf-1", "dr-leaf-2"]:
            config = _render(spec, name)
            lo0_match = re.search(
                r"interface\s+Loopback0.*?ip\s+address\s+(\S+)", config, re.DOTALL
            )
            lo1_match = re.search(
                r"interface\s+Loopback1.*?ip\s+address\s+(\S+)", config, re.DOTALL
            )
            if lo0_match and lo1_match:
                lo0_ip = lo0_match.group(1).split("/")[0]
                lo1_ip = lo1_match.group(1).split("/")[0]
                assert lo0_ip != lo1_ip, (
                    f"{name}: Loopback0 ({lo0_ip}) and Loopback1/VTEP ({lo1_ip}) "
                    "must be different IPs"
                )


# ---------------------------------------------------------------------------
# 5. /31 IP Pair Reciprocal Validation
# ---------------------------------------------------------------------------
class TestP2PLinkReciprocity:
    """Every /31 P2P link must have correct, reciprocal IPs on both ends.

    For a /31, the two valid host IPs differ by XOR 1 on the last octet.
    If one end has .0, the other must have .1. If one has .4, the other must have .5.
    """

    def _extract_interface_ip(self, config: str, interface_name: str) -> str | None:
        """Extract the IP configured on a specific interface."""
        # EOS format: interface EthernetN\n   ip address X.X.X.X/31
        pattern = rf"interface\s+{re.escape(interface_name)}.*?ip\s+address\s+(\S+)"
        match = re.search(pattern, config, re.DOTALL)
        return match.group(1).split("/")[0] if match else None

    # Devices that use EOS-style config (interface X / ip address Y)
    EOS_DEVICES = {
        "dc-spine-1",
        "dc-spine-2",
        "dc-leaf-1",
        "dc-leaf-2",
        "dc-border-1",
        "dc-border-2",
        "dr-leaf-1",
        "dr-leaf-2",
    }

    def test_dc_p2p_links_correct_pairs(self, spec: dict) -> None:
        """Every DC P2P link between EOS devices must have correct /31 pairs in both configs.

        Links to firewalls are excluded — FortiGate uses a different config format.
        Those links are validated separately when FortiGate config parsing is added.
        """
        links = spec["sites"]["dc_east"].get("links", [])

        for link in links:
            if link.get("type") != "p2p":
                continue

            a_dev = link["a_end"]["device"]
            z_dev = link["z_end"]["device"]

            # Only validate links where both ends use EOS config format
            if a_dev not in self.EOS_DEVICES or z_dev not in self.EOS_DEVICES:
                continue

            a_iface = link["a_end"]["interface"]
            a_spec_ip = link["a_end"].get("ipv4", "").split("/")[0]

            z_iface = link["z_end"]["interface"]
            z_spec_ip = link["z_end"].get("ipv4", "").split("/")[0]

            # Skip /31 pair validation if either side is missing an IP
            # (can happen when NetBox has IP conflicts from shared ranges)
            if not a_spec_ip or not z_spec_ip:
                continue

            # Verify the spec IPs are a valid /31 pair
            a_last = int(a_spec_ip.split(".")[-1])
            z_last = int(z_spec_ip.split(".")[-1])
            assert a_last ^ 1 == z_last, (
                f"Spec error: {a_dev}:{a_iface} ({a_spec_ip}) and "
                f"{z_dev}:{z_iface} ({z_spec_ip}) are not a valid /31 pair"
            )

            # Verify the rendered configs match the spec
            a_config = _render(spec, a_dev)
            z_config = _render(spec, z_dev)

            a_rendered_ip = self._extract_interface_ip(a_config, a_iface)
            z_rendered_ip = self._extract_interface_ip(z_config, z_iface)

            assert a_rendered_ip == a_spec_ip, (
                f"{a_dev}:{a_iface} rendered IP {a_rendered_ip} != spec {a_spec_ip}"
            )
            assert z_rendered_ip == z_spec_ip, (
                f"{z_dev}:{z_iface} rendered IP {z_rendered_ip} != spec {z_spec_ip}"
            )


# ---------------------------------------------------------------------------
# 6. Route-Target Consistency Across Sites
# ---------------------------------------------------------------------------
class TestRouteTargetConsistency:
    """Route-targets for the same VNI must be compatible across DC and DR.

    If DC uses RT 65001:10100 and DR uses RT 65201:10100, EVPN routes from
    DC will never be imported at DR (and vice versa). This is a silent
    forwarding failure — BGP sessions are up, EVPN routes are exchanged,
    but no one imports them.
    """

    def _extract_route_targets(self, config: str) -> dict[int, dict[str, set[str]]]:
        """Extract {vlan: {import: set(RT), export: set(RT)}} from config.

        Handles 'route-target both X' (same for import+export),
        'route-target import X', and 'route-target export X'.
        """
        result: dict[int, dict[str, set[str]]] = {}
        current_vlan = None

        for line in config.splitlines():
            stripped = line.strip()
            # Match 'vlan NNN' inside BGP config
            vlan_match = re.match(r"vlan\s+(\d+)", stripped)
            if vlan_match and "interface" not in line and "switchport" not in line:
                current_vlan = int(vlan_match.group(1))
                if current_vlan not in result:
                    result[current_vlan] = {"import": set(), "export": set()}

            if current_vlan is not None:
                rt_both = re.match(r"route-target\s+both\s+(\S+)", stripped)
                rt_import = re.match(r"route-target\s+import\s+(\S+)", stripped)
                rt_export = re.match(r"route-target\s+export\s+(\S+)", stripped)

                if rt_both:
                    result[current_vlan]["import"].add(rt_both.group(1))
                    result[current_vlan]["export"].add(rt_both.group(1))
                elif rt_import:
                    result[current_vlan]["import"].add(rt_import.group(1))
                elif rt_export:
                    result[current_vlan]["export"].add(rt_export.group(1))

        return result

    def test_dc_leaves_compatible_route_targets(self, spec: dict) -> None:
        """DC compute leaves must have compatible RTs for the same VLANs."""
        rt_1 = self._extract_route_targets(_render(spec, "dc-leaf-1"))
        rt_2 = self._extract_route_targets(_render(spec, "dc-leaf-2"))

        for vlan in set(rt_1.keys()) | set(rt_2.keys()):
            if vlan in rt_1 and vlan in rt_2:
                # What leaf-1 exports, leaf-2 must import (and vice versa)
                assert rt_1[vlan]["export"] & rt_2[vlan]["import"], (
                    f"VLAN {vlan}: dc-leaf-1 exports {rt_1[vlan]['export']} "
                    f"but dc-leaf-2 imports {rt_2[vlan]['import']} — no overlap, "
                    "EVPN routes will be silently dropped"
                )
                assert rt_2[vlan]["export"] & rt_1[vlan]["import"], (
                    f"VLAN {vlan}: dc-leaf-2 exports {rt_2[vlan]['export']} "
                    f"but dc-leaf-1 imports {rt_1[vlan]['import']} — no overlap"
                )

    def test_cross_site_route_targets_compatible(self, spec: dict) -> None:
        """DC and DR must have compatible RTs for shared VNIs (VNI 10100 = VLAN 100).

        If DC exports 65001:10100 and DR imports 65201:10100, routes are exchanged
        via EVPN but never imported. Both sites must either use the same RT value
        or have explicit import rules for each other's RTs.
        """
        dc_rt = self._extract_route_targets(_render(spec, "dc-leaf-1"))
        dr_rt = self._extract_route_targets(_render(spec, "dr-leaf-1"))

        # VLAN 100 is the shared tenant network (VNI 10100)
        if 100 in dc_rt and 100 in dr_rt:
            dc_exports = dc_rt[100]["export"]
            dr_imports = dr_rt[100]["import"]
            dr_exports = dr_rt[100]["export"]
            dc_imports = dc_rt[100]["import"]

            assert dc_exports & dr_imports, (
                f"Cross-site RT mismatch for VLAN 100: "
                f"DC exports {dc_exports}, DR imports {dr_imports} — "
                "no overlap means EVPN type-2/type-5 routes from DC are silently "
                "dropped at DR. Both sites must use compatible route-targets."
            )
            assert dr_exports & dc_imports, (
                f"Cross-site RT mismatch for VLAN 100: "
                f"DR exports {dr_exports}, DC imports {dc_imports} — "
                "no overlap means DR routes are silently dropped at DC."
            )


# ---------------------------------------------------------------------------
# 7. No Duplicate Stanza Detection
# ---------------------------------------------------------------------------
class TestNoDuplicateStanzas:
    """Detect duplicate configuration blocks that indicate template rendering bugs."""

    def test_no_duplicate_vlan_definitions(self, spec: dict) -> None:
        """Each VLAN should be defined exactly once per device config."""
        for name in ["dc-leaf-1", "dc-leaf-2", "dr-leaf-1", "dr-leaf-2"]:
            config = _render(spec, name)
            vlan_defs = re.findall(r"^vlan\s+(\d+)\s*$", config, re.MULTILINE)
            duplicates = [v for v in set(vlan_defs) if vlan_defs.count(v) > 1]
            assert not duplicates, (
                f"{name}: duplicate VLAN definitions for VLANs {duplicates} — "
                "template rendering bug"
            )
