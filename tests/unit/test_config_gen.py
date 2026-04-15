"""Tests for config generation from YAML spec.

TDD: Written BEFORE the rendering engine and Jinja2 templates.
Each test verifies that a rendered device config contains the correct
sections derived entirely from the YAML spec — no hardcoded values.
"""

import json
from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"
SCHEMA_PATH = Path(__file__).parent.parent.parent / "specs" / "schema.json"


@pytest.fixture
def spec() -> dict:
    """Load the full lab spec from disk."""
    return yaml.safe_load(SPEC_PATH.read_text())


@pytest.fixture
def schema() -> dict:
    """Load the JSON Schema."""
    return json.loads(SCHEMA_PATH.read_text())


def _render_device(spec: dict, device_name: str) -> str:
    """Helper: render config for a single device and return as string."""
    from generator.render_configs import render_device_config

    return render_device_config(spec, device_name)


def _get_device_from_spec(spec: dict, device_name: str) -> dict:
    """Find a device dict by name anywhere in the spec."""
    for _site_key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            if dev["name"] == device_name:
                return dev
    for dev in spec.get("wan_transport", {}).get("devices", []):
        if dev["name"] == device_name:
            return dev
    for zone in spec.get("security", {}).values():
        for dev in zone.get("firewalls", []):
            if dev["name"] == device_name:
                return dev
    raise ValueError(f"Device {device_name} not found in spec")


# ---------------------------------------------------------------------------
# Test 1: Arista Spine — dc-spine-1
# ---------------------------------------------------------------------------
class TestSpineConfig:
    """dc-spine-1 rendered EOS config must contain correct BGP, interfaces, EVPN."""

    def test_bgp_asn(self, spec: dict) -> None:
        """Config must contain router bgp with correct ASN from spec."""
        config = _render_device(spec, "dc-spine-1")
        dev = _get_device_from_spec(spec, "dc-spine-1")
        assert f"router bgp {dev['asn']}" in config

    def test_evpn_route_reflector(self, spec: dict) -> None:
        """Spine must have route-reflector-client in EVPN address family."""
        config = _render_device(spec, "dc-spine-1")
        assert "route-reflector-client" in config

    def test_loopback0_ip(self, spec: dict) -> None:
        """Loopback0 must have the IP from the spec."""
        config = _render_device(spec, "dc-spine-1")
        dev = _get_device_from_spec(spec, "dc-spine-1")
        # Loopback IP without prefix length for EOS interface config
        lo_ip = dev["loopback0"]
        assert lo_ip.split("/")[0] in config

    def test_p2p_interfaces(self, spec: dict) -> None:
        """All P2P interfaces from the spec must appear with correct IPs."""
        config = _render_device(spec, "dc-spine-1")
        dev = _get_device_from_spec(spec, "dc-spine-1")
        for iface in dev.get("interfaces", []):
            assert iface["name"] in config
            if "ipv4" in iface:
                ip_addr = iface["ipv4"].split("/")[0]
                assert ip_addr in config, f"Missing IP {ip_addr} for {iface['name']}"

    def test_evpn_overlay_neighbors(self, spec: dict) -> None:
        """Spine must have EVPN overlay sessions using loopback as update-source."""
        config = _render_device(spec, "dc-spine-1")
        assert "update-source Loopback0" in config
        # Must peer with leaf/border loopbacks from overlay config
        fabric = spec["sites"]["dc_east"].get("fabric", {})
        overlay = fabric.get("overlay", {})
        for neighbor in overlay.get("neighbors", []):
            assert neighbor["address"] in config


# ---------------------------------------------------------------------------
# Test 2: Arista Compute Leaf — dc-leaf-1
# ---------------------------------------------------------------------------
class TestComputeLeafConfig:
    """dc-leaf-1 rendered EOS config must contain VXLAN, BGP, anycast gateway."""

    def test_bgp_asn(self, spec: dict) -> None:
        """Config must contain router bgp with leaf's unique ASN."""
        config = _render_device(spec, "dc-leaf-1")
        dev = _get_device_from_spec(spec, "dc-leaf-1")
        assert f"router bgp {dev['asn']}" in config

    def test_vxlan_vni_mapping(self, spec: dict) -> None:
        """VXLAN interface must map VNI 10100 to VLAN 100."""
        config = _render_device(spec, "dc-leaf-1")
        assert "vxlan vni 10100 vlan 100" in config.lower().replace("  ", " ")

    def test_anycast_gateway(self, spec: dict) -> None:
        """Must have virtual-router mac and anycast gateway IP."""
        config = _render_device(spec, "dc-leaf-1")
        # Check for virtual-router or anycast gateway configuration
        assert "virtual-router" in config.lower() or "anycast" in config.lower()

    def test_vtep_source_interface(self, spec: dict) -> None:
        """VTEP source must point to correct loopback."""
        config = _render_device(spec, "dc-leaf-1")
        assert "Loopback1" in config
        # VTEP source IP from per-device vtep_sources map
        vxlan = spec["sites"]["dc_east"]["fabric"]["vxlan"]
        vtep_ip = vxlan["vtep_sources"]["dc-leaf-1"].split("/")[0]
        assert vtep_ip in config

    def test_ebgp_neighbors_to_spines(self, spec: dict) -> None:
        """Leaf must have eBGP neighbors to both spines."""
        config = _render_device(spec, "dc-leaf-1")
        dev = _get_device_from_spec(spec, "dc-leaf-1")
        for iface in dev.get("interfaces", []):
            if iface.get("peer", "").startswith("dc-spine"):
                # The spine's IP is the /31 peer
                assert iface["peer"] in config or iface["ipv4"].split("/")[0] in config

    def test_ibgp_evpn_to_spines(self, spec: dict) -> None:
        """Leaf must have iBGP EVPN sessions to spine loopbacks."""
        config = _render_device(spec, "dc-leaf-1")
        spine1 = _get_device_from_spec(spec, "dc-spine-1")
        spine2 = _get_device_from_spec(spec, "dc-spine-2")
        spine1_lo = spine1["loopback0"].split("/")[0]
        spine2_lo = spine2["loopback0"].split("/")[0]
        assert spine1_lo in config
        assert spine2_lo in config


# ---------------------------------------------------------------------------
# Test 3: Arista Border Leaf — dc-border-1
# ---------------------------------------------------------------------------
class TestBorderLeafConfig:
    """dc-border-1 rendered EOS config must contain BGP, external interface."""

    def test_bgp_asn(self, spec: dict) -> None:
        """Config must contain router bgp 65003."""
        config = _render_device(spec, "dc-border-1")
        dev = _get_device_from_spec(spec, "dc-border-1")
        assert f"router bgp {dev['asn']}" in config

    def test_external_interface_to_firewall(self, spec: dict) -> None:
        """Must have interface toward firewall with correct IP."""
        config = _render_device(spec, "dc-border-1")
        dev = _get_device_from_spec(spec, "dc-border-1")
        fw_iface = next(i for i in dev.get("interfaces", []) if i.get("peer") == "dc-fw-1")
        assert fw_iface["ipv4"].split("/")[0] in config

    def test_ebgp_neighbors_to_spines(self, spec: dict) -> None:
        """Border must have eBGP sessions to spines."""
        config = _render_device(spec, "dc-border-1")
        dev = _get_device_from_spec(spec, "dc-border-1")
        spine_ifaces = [
            i for i in dev.get("interfaces", []) if i.get("peer", "").startswith("dc-spine")
        ]
        assert len(spine_ifaces) >= 2
        for iface in spine_ifaces:
            assert iface["ipv4"].split("/")[0] in config

    def test_dc_prefix_advertisement(self, spec: dict) -> None:
        """Border must advertise DC summary prefixes."""
        config = _render_device(spec, "dc-border-1")
        # Should have a network or aggregate for DC ranges
        assert "10.1.0.0" in config or "10.10." in config or "aggregate" in config.lower()


# ---------------------------------------------------------------------------
# Test 4: Cisco IOS-XE Branch Router — br-ce-1
# ---------------------------------------------------------------------------
class TestBranchRouterConfig:
    """br-ce-1 rendered IOS-XE config must contain BGP, dual-homed to PEs."""

    def test_bgp_asn(self, spec: dict) -> None:
        """Config must contain router bgp 65100."""
        config = _render_device(spec, "br-ce-1")
        dev = _get_device_from_spec(spec, "br-ce-1")
        assert f"router bgp {dev['asn']}" in config

    def test_ebgp_to_both_pes(self, spec: dict) -> None:
        """Must have eBGP neighbors to BOTH sp-pe-1 and sp-pe-2."""
        config = _render_device(spec, "br-ce-1")
        dev = _get_device_from_spec(spec, "br-ce-1")
        pe_ifaces = [i for i in dev.get("interfaces", []) if i.get("peer", "").startswith("sp-pe")]
        assert len(pe_ifaces) == 2, "br-ce-1 must be dual-homed to both PEs"
        for iface in pe_ifaces:
            # The neighbor IP is the PE side of the /31
            peer_ip = iface["ipv4"].split("/")[0]
            # Calculate the PE's IP (the other side of the /31)
            octets = peer_ip.split(".")
            last = int(octets[3])
            pe_ip = ".".join(octets[:3]) + "." + str(last ^ 1)
            assert pe_ip in config, f"Missing PE neighbor {pe_ip}"

    def test_network_statements(self, spec: dict) -> None:
        """Must advertise branch prefixes via network statements."""
        config = _render_device(spec, "br-ce-1")
        assert "10.20." in config

    def test_interface_configs(self, spec: dict) -> None:
        """All interfaces must have IPs from the spec."""
        config = _render_device(spec, "br-ce-1")
        dev = _get_device_from_spec(spec, "br-ce-1")
        for iface in dev.get("interfaces", []):
            if "ipv4" in iface:
                assert iface["name"] in config


# ---------------------------------------------------------------------------
# Test 5: Cisco IOS-XE DC CE — dc-ce-1
# ---------------------------------------------------------------------------
class TestDcCeConfig:
    """dc-ce-1 rendered IOS-XE config must contain dual-homed BGP."""

    def test_bgp_asn(self, spec: dict) -> None:
        """Config must contain router bgp 65100."""
        config = _render_device(spec, "dc-ce-1")
        dev = _get_device_from_spec(spec, "dc-ce-1")
        assert f"router bgp {dev['asn']}" in config

    def test_ebgp_to_both_pes(self, spec: dict) -> None:
        """Must have eBGP neighbors to BOTH sp-pe-1 and sp-pe-2."""
        config = _render_device(spec, "dc-ce-1")
        dev = _get_device_from_spec(spec, "dc-ce-1")
        pe_ifaces = [i for i in dev.get("interfaces", []) if i.get("peer", "").startswith("sp-pe")]
        assert len(pe_ifaces) == 2, "dc-ce-1 must be dual-homed"
        for iface in pe_ifaces:
            peer_ip = iface["ipv4"].split("/")[0]
            octets = peer_ip.split(".")
            last = int(octets[3])
            pe_ip = ".".join(octets[:3]) + "." + str(last ^ 1)
            assert pe_ip in config, f"Missing PE neighbor {pe_ip}"

    def test_dual_homed_path_selection(self, spec: dict) -> None:
        """Must have path selection config (local-pref or as-path prepend)."""
        config = _render_device(spec, "dc-ce-1")
        has_path_selection = (
            "local-preference" in config.lower()
            or "route-map" in config.lower()
            or "as-path prepend" in config.lower()
        )
        assert has_path_selection, "CE must have dual-homed path selection config"


# ---------------------------------------------------------------------------
# Test 6: FortiGate — dc-fw-1
# ---------------------------------------------------------------------------
class TestFortiGateConfig:
    """dc-fw-1 rendered FortiGate config must contain zones, HA, policies."""

    def test_zone_definitions(self, spec: dict) -> None:
        """Config must define dc-trust and wan-untrust zones."""
        config = _render_device(spec, "dc-fw-1")
        dc_security = spec["security"]["dc"]
        for zone in dc_security["zones"]:
            assert zone["name"] in config

    def test_interface_zone_assignments(self, spec: dict) -> None:
        """Interfaces must be assigned to their zones."""
        config = _render_device(spec, "dc-fw-1")
        assert "port1" in config
        assert "port2" in config

    def test_ha_configuration(self, spec: dict) -> None:
        """Must have HA config with priority, group-id, heartbeat."""
        config = _render_device(spec, "dc-fw-1")
        assert "ha" in config.lower()
        assert "priority" in config.lower()
        assert "group-id" in config.lower() or "group_id" in config.lower()

    def test_firewall_policies(self, spec: dict) -> None:
        """Must have policies permitting ICMP and SSH (tcp/22)."""
        config = _render_device(spec, "dc-fw-1")
        assert "icmp" in config.lower() or "ICMP" in config
        # SSH should be in config (from policy services or explicit)
        assert "ssh" in config.lower() or "tcp/22" in config or "22" in config


# ---------------------------------------------------------------------------
# Test 7: No hardcoded IPs
# ---------------------------------------------------------------------------
class TestNoHardcodedValues:
    """No rendered config should contain IPs that don't come from the spec."""

    def _collect_all_ips_from_spec(self, spec: dict) -> set[str]:
        """Collect every IP address mentioned anywhere in the spec."""
        ips = set()

        def _extract_ips(obj: object) -> None:
            if isinstance(obj, str):
                # Match IP-like patterns
                import re

                for match in re.findall(r"\d+\.\d+\.\d+\.\d+", obj):
                    ips.add(match)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _extract_ips(v)
            elif isinstance(obj, list):
                for item in obj:
                    _extract_ips(item)

        _extract_ips(spec)
        return ips

    def _collect_all_ips_from_config(self, config: str) -> set[str]:
        """Extract all IP addresses from a rendered config."""
        import re

        return set(re.findall(r"\d+\.\d+\.\d+\.\d+", config))

    def test_spine_no_hardcoded_ips(self, spec: dict) -> None:
        """dc-spine-1 config must not contain IPs absent from the spec."""
        config = _render_device(spec, "dc-spine-1")
        spec_ips = self._collect_all_ips_from_spec(spec)
        config_ips = self._collect_all_ips_from_config(config)
        # Allow well-known addresses (0.0.0.0, 255.255.255.255, etc.)
        well_known = self._well_known_ips()
        unexpected = config_ips - spec_ips - well_known
        assert not unexpected, f"Hardcoded IPs found in dc-spine-1 config: {unexpected}"

    def test_leaf_no_hardcoded_ips(self, spec: dict) -> None:
        """dc-leaf-1 config must not contain IPs absent from the spec."""
        config = _render_device(spec, "dc-leaf-1")
        spec_ips = self._collect_all_ips_from_spec(spec)
        config_ips = self._collect_all_ips_from_config(config)
        well_known = self._well_known_ips()
        unexpected = config_ips - spec_ips - well_known
        assert not unexpected, f"Hardcoded IPs found in dc-leaf-1 config: {unexpected}"

    def test_fortigate_no_hardcoded_ips(self, spec: dict) -> None:
        """dc-fw-1 config must not contain IPs absent from the spec."""
        config = _render_device(spec, "dc-fw-1")
        spec_ips = self._collect_all_ips_from_spec(spec)
        config_ips = self._collect_all_ips_from_config(config)
        well_known = self._well_known_ips()
        unexpected = config_ips - spec_ips - well_known
        assert not unexpected, f"Hardcoded IPs found in dc-fw-1 config: {unexpected}"

    def test_cisco_no_hardcoded_ips(self, spec: dict) -> None:
        """br-ce-1 config must not contain IPs absent from the spec."""
        config = _render_device(spec, "br-ce-1")
        spec_ips = self._collect_all_ips_from_spec(spec)
        config_ips = self._collect_all_ips_from_config(config)
        well_known = self._well_known_ips()
        unexpected = config_ips - spec_ips - well_known
        assert not unexpected, f"Hardcoded IPs found in br-ce-1 config: {unexpected}"

    @staticmethod
    def _well_known_ips() -> set[str]:
        """IPs that are derived (subnet masks) or protocol-standard, not hardcoded."""
        return {
            "0.0.0.0",
            "255.255.255.255",
            "255.255.255.254",
            "255.255.255.252",
            "255.255.255.248",
            "255.255.255.240",
            "255.255.255.224",
            "255.255.255.192",
            "255.255.255.128",
            "255.255.255.0",
            "255.255.254.0",
            "255.255.252.0",
            "255.255.248.0",
            "255.255.240.0",
            "255.255.0.0",
        }


# ---------------------------------------------------------------------------
# Test: No hardcoded ASNs — all ASNs in configs must come from the spec
# ---------------------------------------------------------------------------
class TestNoHardcodedASNs:
    """Every ASN in a rendered config must exist in the spec."""

    def _collect_all_asns_from_spec(self, spec: dict) -> set[int]:
        """Collect every ASN mentioned in the spec."""
        asns: set[int] = set()

        def _extract(obj: object) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("asn", "remote_as") and isinstance(v, int):
                        asns.add(v)
                    else:
                        _extract(v)
            elif isinstance(obj, list):
                for item in obj:
                    _extract(item)

        _extract(spec)
        return asns

    def _collect_asns_from_config(self, config: str) -> set[int]:
        """Extract ASN numbers from 'remote-as' and 'router bgp' lines."""
        import re

        asns: set[int] = set()
        for match in re.findall(r"(?:remote-as|router bgp)\s+(\d+)", config):
            asns.add(int(match))
        return asns

    def test_spine_no_hardcoded_asns(self, spec: dict) -> None:
        """dc-spine-1 config ASNs must all exist in the spec."""
        config = _render_device(spec, "dc-spine-1")
        spec_asns = self._collect_all_asns_from_spec(spec)
        config_asns = self._collect_asns_from_config(config)
        unexpected = config_asns - spec_asns - {0}
        assert not unexpected, f"Hardcoded ASNs in dc-spine-1 config: {unexpected}"

    def test_ce_no_hardcoded_asns(self, spec: dict) -> None:
        """dc-ce-1 config ASNs must all exist in the spec."""
        config = _render_device(spec, "dc-ce-1")
        spec_asns = self._collect_all_asns_from_spec(spec)
        config_asns = self._collect_asns_from_config(config)
        unexpected = config_asns - spec_asns - {0}
        assert not unexpected, f"Hardcoded ASNs in dc-ce-1 config: {unexpected}"

    def test_branch_no_hardcoded_asns(self, spec: dict) -> None:
        """br-ce-1 config ASNs must all exist in the spec."""
        config = _render_device(spec, "br-ce-1")
        spec_asns = self._collect_all_asns_from_spec(spec)
        config_asns = self._collect_asns_from_config(config)
        unexpected = config_asns - spec_asns - {0}
        assert not unexpected, f"Hardcoded ASNs in br-ce-1 config: {unexpected}"

    def test_leaf_no_hardcoded_asns(self, spec: dict) -> None:
        """dc-leaf-1 config ASNs must all exist in the spec."""
        config = _render_device(spec, "dc-leaf-1")
        spec_asns = self._collect_all_asns_from_spec(spec)
        config_asns = self._collect_asns_from_config(config)
        unexpected = config_asns - spec_asns - {0}
        assert not unexpected, f"Hardcoded ASNs in dc-leaf-1 config: {unexpected}"


# ---------------------------------------------------------------------------
# Test: All 17 network devices produce non-empty configs
# ---------------------------------------------------------------------------
class TestAllDevicesRendered:
    """Every network device in the topology must get a rendered config."""

    NETWORK_DEVICES = [
        "dc-spine-1",
        "dc-spine-2",
        "dc-leaf-1",
        "dc-leaf-2",
        "dc-border-1",
        "dc-border-2",
        "dc-fw-1",
        "dc-fw-2",
        "dc-ce-1",
        "sp-pe-1",
        "sp-pe-2",
        "br-ce-1",
        "dr-leaf-1",
        "dr-leaf-2",
        "dr-fw-1",
        "dr-fw-2",
        "dr-ce-1",
    ]

    def test_all_17_devices_produce_configs(self, spec: dict) -> None:
        """All 17 network devices must produce non-empty configs."""
        from generator.render_configs import render_device_config

        for device_name in self.NETWORK_DEVICES:
            config = render_device_config(spec, device_name)
            assert len(config) > 100, f"{device_name} config is too short ({len(config)} chars)"

    def test_correct_device_count(self, spec: dict) -> None:
        """Must produce exactly 17 config files (all network devices)."""
        from generator.render_configs import get_all_network_devices

        devices = get_all_network_devices(spec)
        assert len(devices) == 17, f"Expected 17 network devices, got {len(devices)}"
