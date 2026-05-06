"""Tests for the NetBox-to-YAML spec generator.

TDD: These tests define expected generator behavior using mocked NetBox
API responses. Written BEFORE the generator implementation.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jsonschema import validate

# ---------------------------------------------------------------------------
# Fixtures — mock NetBox API responses
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).parent.parent.parent / "specs" / "schema.json"


@pytest.fixture
def schema() -> dict:
    """Load the JSON Schema from disk."""
    return json.loads(SCHEMA_PATH.read_text())


def _make_site(id: int, name: str, slug: str) -> MagicMock:
    site = MagicMock()
    site.id = id
    site.name = name
    site.slug = slug
    site.status.value = "active"
    return site


def _make_device_role(id: int, name: str, slug: str) -> MagicMock:
    role = MagicMock()
    role.id = id
    role.name = name
    role.slug = slug
    return role


def _make_device(
    id: int,
    name: str,
    site_slug: str,
    role_slug: str,
    platform_slug: str,
    *,
    config_context: dict | None = None,
    custom_fields: dict | None = None,
) -> MagicMock:
    dev = MagicMock()
    dev.id = id
    dev.name = name
    dev.site.slug = site_slug
    dev.role.slug = role_slug
    dev.platform.slug = platform_slug
    dev.config_context = config_context or {}
    dev.custom_fields = custom_fields or {}
    dev.tags = []
    dev.status.value = "active"
    return dev


def _make_interface(
    id: int,
    name: str,
    device_name: str,
    device_id: int,
    *,
    description: str = "",
) -> MagicMock:
    iface = MagicMock()
    iface.id = id
    iface.name = name
    iface.device.name = device_name
    iface.device.id = device_id
    iface.description = description
    iface.enabled = True
    iface.type.value = "1000base-t"
    return iface


def _make_ip(
    id: int,
    address: str,
    interface_id: int,
    interface_name: str,
    device_name: str,
    device_id: int,
) -> MagicMock:
    ip = MagicMock()
    ip.id = id
    ip.address = address
    ip.assigned_object_id = interface_id
    ip.assigned_object_type = "dcim.interface"
    ip.assigned_object.id = interface_id
    ip.assigned_object.name = interface_name
    ip.assigned_object.device.name = device_name
    ip.assigned_object.device.id = device_id
    return ip


def _make_prefix(id: int, prefix: str, description: str, site_slug: str | None = None) -> MagicMock:
    pfx = MagicMock()
    pfx.id = id
    pfx.prefix = prefix
    pfx.description = description
    if site_slug:
        pfx.site.slug = site_slug
    else:
        pfx.site = None
    return pfx


def _make_cable(id: int, a_device: str, a_iface: str, z_device: str, z_iface: str) -> MagicMock:
    cable = MagicMock()
    cable.id = id

    a_term = MagicMock()
    a_term.name = a_iface
    a_term.device.name = a_device

    z_term = MagicMock()
    z_term.name = z_iface
    z_term.device.name = z_device

    cable.a_terminations = [a_term]
    cable.b_terminations = [z_term]
    return cable


# ---------------------------------------------------------------------------
# Build a complete mock NetBox dataset representing the full 21-node topology
# ---------------------------------------------------------------------------

# Sites
SITE_DC_EAST = _make_site(1, "DC-East", "dc-east")
SITE_BRANCH = _make_site(2, "Branch-01", "branch-01")
SITE_DR_WEST = _make_site(3, "DR-West", "dr-west")

# Device roles
ROLE_SPINE = _make_device_role(1, "Spine", "spine")
ROLE_LEAF = _make_device_role(2, "Leaf", "leaf")
ROLE_BORDER = _make_device_role(3, "Border Leaf", "border-leaf")
ROLE_CE = _make_device_role(4, "CE Router", "ce")
ROLE_PE = _make_device_role(5, "PE Router", "pe")
ROLE_FW = _make_device_role(6, "Firewall", "firewall")
ROLE_HOST = _make_device_role(7, "Host", "host")

# --- DC-East devices (8) ---
DC_SPINE_1 = _make_device(
    1,
    "dc-spine-1",
    "dc-east",
    "spine",
    "arista_eos",
    config_context={"agent_boundary": "managed", "bgp_config": {"asn": 65000, "role": "rr"}},
    custom_fields={"bgp_asn": 65000, "evpn_role": "rr"},
)
DC_SPINE_2 = _make_device(
    2,
    "dc-spine-2",
    "dc-east",
    "spine",
    "arista_eos",
    config_context={"agent_boundary": "managed", "bgp_config": {"asn": 65000, "role": "rr"}},
    custom_fields={"bgp_asn": 65000, "evpn_role": "rr"},
)
DC_LEAF_1 = _make_device(
    3,
    "dc-leaf-1",
    "dc-east",
    "leaf",
    "arista_eos",
    config_context={
        "agent_boundary": "managed",
        "bgp_config": {"asn": 65001, "role": "client"},
        "vxlan_config": {
            "vtep_ip": "10.1.2.11/32",
            "vtep_source_interface": "Loopback1",
            "vni_mappings": [
                {"vni": 10100, "vlan": 100, "name": "SERVERS_A", "subnet": "10.10.1.0/24"}
            ],
        },
    },
    custom_fields={"bgp_asn": 65001, "evpn_role": "client", "vtep_source_interface": "Loopback1"},
)
DC_LEAF_2 = _make_device(
    4,
    "dc-leaf-2",
    "dc-east",
    "leaf",
    "arista_eos",
    config_context={
        "agent_boundary": "managed",
        "bgp_config": {"asn": 65002, "role": "client"},
        "vxlan_config": {
            "vtep_ip": "10.1.2.12/32",
            "vtep_source_interface": "Loopback1",
            "vni_mappings": [
                {"vni": 10200, "vlan": 200, "name": "SERVERS_B", "subnet": "10.10.2.0/24"}
            ],
        },
    },
    custom_fields={"bgp_asn": 65002, "evpn_role": "client", "vtep_source_interface": "Loopback1"},
)
DC_BORDER_1 = _make_device(
    5,
    "dc-border-1",
    "dc-east",
    "border-leaf",
    "arista_eos",
    config_context={
        "agent_boundary": "managed",
        "bgp_config": {"asn": 65003},
        "observed_interfaces": ["Ethernet3"],
    },
    custom_fields={"bgp_asn": 65003, "evpn_role": "none"},
)
DC_BORDER_2 = _make_device(
    6,
    "dc-border-2",
    "dc-east",
    "border-leaf",
    "arista_eos",
    config_context={
        "agent_boundary": "managed",
        "bgp_config": {"asn": 65004},
        "observed_interfaces": ["Ethernet3"],
    },
    custom_fields={"bgp_asn": 65004, "evpn_role": "none"},
)
DC_HOST_1 = _make_device(
    7,
    "dc-host-1",
    "dc-east",
    "host",
    "linux",
    config_context={"agent_boundary": "excluded"},
)
DC_HOST_2 = _make_device(
    8,
    "dc-host-2",
    "dc-east",
    "host",
    "linux",
    config_context={"agent_boundary": "excluded"},
)

# --- DC Security (4 firewalls — 2 DC, 2 DR) ---
DC_FW_1 = _make_device(
    9,
    "dc-fw-1",
    "dc-east",
    "firewall",
    "fortinet_fortios",
    config_context={
        "agent_boundary": "excluded",
        "ha_config": {"role": "active", "peer": "dc-fw-2"},
    },
)
DC_FW_2 = _make_device(
    10,
    "dc-fw-2",
    "dc-east",
    "firewall",
    "fortinet_fortios",
    config_context={
        "agent_boundary": "excluded",
        "ha_config": {"role": "standby", "peer": "dc-fw-1"},
    },
)

# --- WAN transport (3 Cisco + 1 already counted) ---
DC_CE_1 = _make_device(
    11,
    "dc-ce-1",
    "dc-east",
    "ce",
    "cisco_iosxe",
    config_context={"agent_boundary": "excluded", "bgp_config": {"asn": 65100}},
    custom_fields={"bgp_asn": 65100},
)
SP_PE_1 = _make_device(
    12,
    "sp-pe-1",
    "dc-east",
    "pe",
    "cisco_iosxe",
    config_context={"agent_boundary": "excluded", "bgp_config": {"asn": 64500}},
    custom_fields={"bgp_asn": 64500},
)
SP_PE_2 = _make_device(
    13,
    "sp-pe-2",
    "dc-east",
    "pe",
    "cisco_iosxe",
    config_context={"agent_boundary": "excluded", "bgp_config": {"asn": 64500}},
    custom_fields={"bgp_asn": 64500},
)

# --- Branch-01 devices (2) ---
BR_CE_1 = _make_device(
    14,
    "br-ce-1",
    "branch-01",
    "ce",
    "cisco_iosxe",
    config_context={"agent_boundary": "managed", "bgp_config": {"asn": 65100}},
    custom_fields={"bgp_asn": 65100},
)
BR_HOST_1 = _make_device(
    15,
    "br-host-1",
    "branch-01",
    "host",
    "linux",
    config_context={"agent_boundary": "excluded"},
)

# --- DR-West devices (5) ---
DR_LEAF_1 = _make_device(
    16,
    "dr-leaf-1",
    "dr-west",
    "leaf",
    "arista_eos",
    config_context={
        "agent_boundary": "managed",
        "bgp_config": {"asn": 65201},
        "observed_interfaces": ["Ethernet2"],
    },
    custom_fields={"bgp_asn": 65201, "evpn_role": "client"},
)
DR_LEAF_2 = _make_device(
    17,
    "dr-leaf-2",
    "dr-west",
    "leaf",
    "arista_eos",
    config_context={
        "agent_boundary": "managed",
        "bgp_config": {"asn": 65202},
        "observed_interfaces": ["Ethernet2"],
    },
    custom_fields={"bgp_asn": 65202, "evpn_role": "client"},
)
DR_FW_1 = _make_device(
    18,
    "dr-fw-1",
    "dr-west",
    "firewall",
    "fortinet_fortios",
    config_context={
        "agent_boundary": "excluded",
        "ha_config": {"role": "active", "peer": "dr-fw-2"},
    },
)
DR_FW_2 = _make_device(
    19,
    "dr-fw-2",
    "dr-west",
    "firewall",
    "fortinet_fortios",
    config_context={
        "agent_boundary": "excluded",
        "ha_config": {"role": "standby", "peer": "dr-fw-1"},
    },
)
DR_CE_1 = _make_device(
    20,
    "dr-ce-1",
    "dr-west",
    "ce",
    "cisco_iosxe",
    config_context={"agent_boundary": "excluded", "bgp_config": {"asn": 65100}},
    custom_fields={"bgp_asn": 65100},
)
DR_HOST_1 = _make_device(
    21,
    "dr-host-1",
    "dr-west",
    "host",
    "linux",
    config_context={"agent_boundary": "excluded"},
)

ALL_DEVICES = [
    DC_SPINE_1,
    DC_SPINE_2,
    DC_LEAF_1,
    DC_LEAF_2,
    DC_BORDER_1,
    DC_BORDER_2,
    DC_HOST_1,
    DC_HOST_2,
    DC_FW_1,
    DC_FW_2,
    DC_CE_1,
    SP_PE_1,
    SP_PE_2,
    BR_CE_1,
    BR_HOST_1,
    DR_LEAF_1,
    DR_LEAF_2,
    DR_FW_1,
    DR_FW_2,
    DR_CE_1,
    DR_HOST_1,
]

# --- Interfaces (selected subset — enough to validate generator logic) ---
ALL_INTERFACES = [
    _make_interface(1, "Loopback0", "dc-spine-1", 1),
    _make_interface(2, "Ethernet1", "dc-spine-1", 1, description="to dc-leaf-1"),
    _make_interface(3, "Ethernet2", "dc-spine-1", 1, description="to dc-leaf-2"),
    _make_interface(4, "Loopback0", "dc-spine-2", 2),
    _make_interface(5, "Ethernet1", "dc-spine-2", 2, description="to dc-leaf-1"),
    _make_interface(6, "Loopback0", "dc-leaf-1", 3),
    _make_interface(7, "Loopback1", "dc-leaf-1", 3, description="VTEP source"),
    _make_interface(8, "Ethernet1", "dc-leaf-1", 3, description="to dc-spine-1"),
    _make_interface(9, "Loopback0", "dc-leaf-2", 4),
    _make_interface(10, "Loopback1", "dc-leaf-2", 4, description="VTEP source"),
    _make_interface(11, "Loopback0", "dc-border-1", 5),
    _make_interface(12, "Ethernet3", "dc-border-1", 5, description="to dc-fw-1"),
    _make_interface(13, "Loopback0", "dc-border-2", 6),
    _make_interface(14, "eth0", "dc-host-1", 7),
    _make_interface(15, "eth0", "dc-host-2", 8),
    _make_interface(16, "port1", "dc-fw-1", 9),
    _make_interface(17, "port2", "dc-fw-1", 9),
    _make_interface(18, "port1", "dc-fw-2", 10),
    _make_interface(19, "GigabitEthernet1", "dc-ce-1", 11),
    _make_interface(20, "GigabitEthernet1", "sp-pe-1", 12),
    _make_interface(21, "GigabitEthernet1", "sp-pe-2", 13),
    _make_interface(22, "Loopback0", "br-ce-1", 14),
    _make_interface(23, "GigabitEthernet1", "br-ce-1", 14),
    _make_interface(24, "eth0", "br-host-1", 15),
    _make_interface(25, "Loopback0", "dr-leaf-1", 16),
    _make_interface(26, "Loopback0", "dr-leaf-2", 17),
    _make_interface(27, "port1", "dr-fw-1", 18),
    _make_interface(28, "port1", "dr-fw-2", 19),
    _make_interface(29, "GigabitEthernet1", "dr-ce-1", 20),
    _make_interface(30, "eth0", "dr-host-1", 21),
]

# --- IP addresses ---
ALL_IPS = [
    _make_ip(1, "10.1.0.1/32", 1, "Loopback0", "dc-spine-1", 1),
    # P2P IPs on spine-1 physical interfaces for contract gap tests
    _make_ip(100, "10.1.1.0/31", 2, "Ethernet1", "dc-spine-1", 1),
    _make_ip(101, "10.1.1.2/31", 3, "Ethernet2", "dc-spine-1", 1),
    _make_ip(2, "10.1.0.2/32", 4, "Loopback0", "dc-spine-2", 2),
    _make_ip(3, "10.1.0.11/32", 6, "Loopback0", "dc-leaf-1", 3),
    _make_ip(4, "10.1.2.11/32", 7, "Loopback1", "dc-leaf-1", 3),
    _make_ip(5, "10.1.0.12/32", 9, "Loopback0", "dc-leaf-2", 4),
    _make_ip(6, "10.1.2.12/32", 10, "Loopback1", "dc-leaf-2", 4),
    _make_ip(7, "10.1.0.13/32", 11, "Loopback0", "dc-border-1", 5),
    _make_ip(8, "10.1.0.14/32", 13, "Loopback0", "dc-border-2", 6),
    _make_ip(9, "10.10.1.10/24", 14, "eth0", "dc-host-1", 7),
    _make_ip(10, "10.10.2.10/24", 15, "eth0", "dc-host-2", 8),
    _make_ip(11, "172.16.0.1/32", 19, "GigabitEthernet1", "dc-ce-1", 11),
    _make_ip(12, "172.16.0.11/32", 20, "GigabitEthernet1", "sp-pe-1", 12),
    _make_ip(13, "172.16.0.12/32", 21, "GigabitEthernet1", "sp-pe-2", 13),
    _make_ip(14, "10.20.0.1/32", 22, "Loopback0", "br-ce-1", 14),
    _make_ip(15, "10.20.1.10/24", 24, "eth0", "br-host-1", 15),
    _make_ip(16, "10.31.0.1/32", 25, "Loopback0", "dr-leaf-1", 16),
    _make_ip(17, "10.31.0.2/32", 26, "Loopback0", "dr-leaf-2", 17),
    _make_ip(18, "10.30.1.10/24", 30, "eth0", "dr-host-1", 21),
]

# --- Prefixes ---
ALL_PREFIXES = [
    _make_prefix(1, "10.1.0.0/24", "DC underlay loopbacks", "dc-east"),
    _make_prefix(2, "10.1.1.0/24", "DC underlay P2P", "dc-east"),
    _make_prefix(3, "10.1.2.0/24", "DC VTEP loopbacks", "dc-east"),
    _make_prefix(4, "10.10.1.0/24", "DC overlay VNI 10100", "dc-east"),
    _make_prefix(5, "10.10.2.0/24", "DC overlay VNI 10200", "dc-east"),
    _make_prefix(6, "10.99.0.0/24", "Border-to-FW transit", "dc-east"),
    _make_prefix(7, "10.99.1.0/24", "FW-to-CE transit", "dc-east"),
    _make_prefix(8, "172.16.0.0/24", "SP transport"),
    _make_prefix(9, "10.20.0.0/16", "Branch addressing", "branch-01"),
    _make_prefix(10, "10.30.0.0/16", "DR overlay", "dr-west"),
    _make_prefix(11, "10.31.0.0/24", "DR underlay", "dr-west"),
]

# --- Cables (selected subset) ---
ALL_CABLES = [
    _make_cable(1, "dc-spine-1", "Ethernet1", "dc-leaf-1", "Ethernet1"),
    _make_cable(2, "dc-spine-1", "Ethernet2", "dc-leaf-2", "Ethernet1"),
    _make_cable(3, "dc-spine-2", "Ethernet1", "dc-leaf-1", "Ethernet2"),
    _make_cable(4, "dc-border-1", "Ethernet3", "dc-fw-1", "port1"),
    _make_cable(5, "dc-border-2", "Ethernet3", "dc-fw-2", "port1"),
]


def _build_mock_api() -> MagicMock:
    """Build a fully mocked pynetbox Api instance."""
    api = MagicMock()

    api.dcim.sites.all.return_value = [SITE_DC_EAST, SITE_BRANCH, SITE_DR_WEST]
    api.dcim.sites.filter.side_effect = lambda slug=None: [
        s for s in [SITE_DC_EAST, SITE_BRANCH, SITE_DR_WEST] if s.slug == slug
    ]

    api.dcim.devices.all.return_value = ALL_DEVICES
    api.dcim.devices.filter.side_effect = lambda site=None, **kw: (
        [d for d in ALL_DEVICES if d.site.slug == site] if site else ALL_DEVICES
    )

    api.dcim.device_roles.all.return_value = [
        ROLE_SPINE,
        ROLE_LEAF,
        ROLE_BORDER,
        ROLE_CE,
        ROLE_PE,
        ROLE_FW,
        ROLE_HOST,
    ]

    api.dcim.interfaces.all.return_value = ALL_INTERFACES
    api.dcim.interfaces.filter.side_effect = lambda device_id=None, **kw: (
        [i for i in ALL_INTERFACES if i.device.id == device_id] if device_id else ALL_INTERFACES
    )

    api.ipam.ip_addresses.all.return_value = ALL_IPS
    api.ipam.ip_addresses.filter.side_effect = lambda device_id=None, **kw: (
        [ip for ip in ALL_IPS if ip.assigned_object.device.id == device_id]
        if device_id
        else ALL_IPS
    )

    api.ipam.prefixes.all.return_value = ALL_PREFIXES
    api.ipam.prefixes.filter.side_effect = lambda site=None, **kw: (
        [p for p in ALL_PREFIXES if p.site and p.site.slug == site] if site else ALL_PREFIXES
    )

    api.dcim.cables.all.return_value = ALL_CABLES

    return api


@pytest.fixture
def mock_api() -> MagicMock:
    """Return a fully mocked pynetbox Api."""
    return _build_mock_api()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGeneratorProducesValidSpec:
    """Given a mocked NetBox with 3 sites and 21 devices, the generator
    must produce a YAML spec that passes JSON schema validation."""

    def test_full_spec_validates_against_schema(self, mock_api: MagicMock, schema: dict) -> None:
        """The generated spec must pass JSON Schema validation."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        validate(instance=spec, schema=schema)

    def test_spec_contains_all_21_devices(self, mock_api: MagicMock) -> None:
        """The generated spec must contain all 21 devices across all sections."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)

        all_device_names = set()
        # Collect from sites
        for site_key in ("dc_east", "branch_01", "dr_west"):
            if site_key in spec.get("sites", {}):
                for dev in spec["sites"][site_key].get("devices", []):
                    all_device_names.add(dev["name"])

        # Collect from wan_transport
        for dev in spec.get("wan_transport", {}).get("devices", []):
            all_device_names.add(dev["name"])

        # Collect from security
        for zone in ("dc", "dr"):
            for dev in spec.get("security", {}).get(zone, {}).get("firewalls", []):
                all_device_names.add(dev["name"])

        assert len(all_device_names) == 21, (
            f"Expected 21 devices, got {len(all_device_names)}: {all_device_names}"
        )

    def test_spec_has_three_sites(self, mock_api: MagicMock) -> None:
        """The spec must have dc_east, branch_01, and dr_west."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        assert "dc_east" in spec["sites"]
        assert "branch_01" in spec["sites"]
        assert "dr_west" in spec["sites"]


class TestAgentBoundary:
    """Generator must correctly tag devices as managed/observed/excluded
    based on config context."""

    def test_managed_devices(self, mock_api: MagicMock) -> None:
        """9 managed devices must be listed."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        managed = spec["agent"]["boundary"]["managed"]
        expected = {
            "dc-spine-1",
            "dc-spine-2",
            "dc-leaf-1",
            "dc-leaf-2",
            "dc-border-1",
            "dc-border-2",
            "br-ce-1",
            "dr-leaf-1",
            "dr-leaf-2",
        }
        assert set(managed) == expected

    def test_observed_interfaces(self, mock_api: MagicMock) -> None:
        """Observed list must contain border/leaf external interfaces toward firewalls."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        observed = spec["agent"]["boundary"]["observed"]
        assert len(observed) == 4
        # Must include observed_interfaces from config context
        assert "dc-border-1:Ethernet3" in observed
        assert "dc-border-2:Ethernet3" in observed
        assert "dr-leaf-1:Ethernet2" in observed
        assert "dr-leaf-2:Ethernet2" in observed

    def test_excluded_devices(self, mock_api: MagicMock) -> None:
        """8 excluded devices (firewalls, CEs, PEs)."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        excluded = spec["agent"]["boundary"]["excluded"]
        expected = {
            "dc-fw-1",
            "dc-fw-2",
            "dr-fw-1",
            "dr-fw-2",
            "dc-ce-1",
            "dr-ce-1",
            "sp-pe-1",
            "sp-pe-2",
        }
        assert set(excluded) == expected


class TestAddressing:
    """Generator must build addressing sections from NetBox prefixes."""

    def test_dc_underlay_prefix_present(self, mock_api: MagicMock) -> None:
        """DC underlay loopbacks (10.1.0.0/24) must be in the spec."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dc_devices = spec["sites"]["dc_east"]["devices"]
        # Spine-1 loopback should be 10.1.0.1/32
        spine1 = next(d for d in dc_devices if d["name"] == "dc-spine-1")
        assert spine1["loopback0"] == "10.1.0.1/32"

    def test_branch_device_addressing(self, mock_api: MagicMock) -> None:
        """Branch CE must have correct loopback from 10.20.0.0/16 range."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        br_devices = spec["sites"]["branch_01"]["devices"]
        br_ce = next(d for d in br_devices if d["name"] == "br-ce-1")
        assert br_ce["loopback0"] == "10.20.0.1/32"

    def test_dr_underlay_addressing(self, mock_api: MagicMock) -> None:
        """DR leaves must have loopbacks from 10.31.0.0/24 range."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dr_devices = spec["sites"]["dr_west"]["devices"]
        dr_leaf1 = next(d for d in dr_devices if d["name"] == "dr-leaf-1")
        assert dr_leaf1["loopback0"] == "10.31.0.1/32"


class TestBGPSessions:
    """Generator must build BGP config from config contexts / custom fields."""

    def test_spine_has_correct_asn(self, mock_api: MagicMock) -> None:
        """DC spines must have AS 65000."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dc_devices = spec["sites"]["dc_east"]["devices"]
        spine1 = next(d for d in dc_devices if d["name"] == "dc-spine-1")
        assert spine1["asn"] == 65000

    def test_leaf_has_unique_asn(self, mock_api: MagicMock) -> None:
        """Each DC leaf must have a unique per-leaf ASN."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dc_devices = spec["sites"]["dc_east"]["devices"]
        leaf1 = next(d for d in dc_devices if d["name"] == "dc-leaf-1")
        leaf2 = next(d for d in dc_devices if d["name"] == "dc-leaf-2")
        assert leaf1["asn"] == 65001
        assert leaf2["asn"] == 65002
        assert leaf1["asn"] != leaf2["asn"]


class TestErrorHandling:
    """Generator must handle error conditions gracefully."""

    def test_unreachable_netbox_raises(self) -> None:
        """If NetBox is unreachable, generator must raise a clear exception."""
        from generator.netbox_to_spec import generate_spec

        api = MagicMock()
        api.dcim.sites.all.side_effect = ConnectionError("Connection refused")

        with pytest.raises(ConnectionError):
            generate_spec(api)

    def test_devices_without_ips_are_skipped_gracefully(self, mock_api: MagicMock) -> None:
        """Devices with no assigned IPs should not break the generator
        or produce invalid spec entries (no loopback0 field)."""
        from generator.netbox_to_spec import generate_spec

        # Host devices (dc-host-1, etc.) have IPs on eth0 but not loopback0.
        # The generator should still produce valid output.
        spec = generate_spec(mock_api)
        dc_devices = spec["sites"]["dc_east"]["devices"]
        host1 = next(d for d in dc_devices if d["name"] == "dc-host-1")
        # Host should not have a loopback0 key (no loopback interface)
        assert "loopback0" not in host1 or host1.get("loopback0") is None


class TestVXLAN:
    """Generator must populate VXLAN section from config contexts."""

    def test_vni_mappings_present(self, mock_api: MagicMock) -> None:
        """DC fabric config must include VNI 10100 and 10200 mappings."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        fabric = spec["sites"]["dc_east"].get("fabric", {})
        vxlan = fabric.get("vxlan", {})
        vni_mappings = vxlan.get("vni_mappings", [])
        vnis = {m["vni"] for m in vni_mappings}
        assert 10100 in vnis, f"VNI 10100 not found in {vni_mappings}"
        assert 10200 in vnis, f"VNI 10200 not found in {vni_mappings}"

    def test_vtep_loopbacks_in_correct_range(self, mock_api: MagicMock) -> None:
        """VTEP source addresses must be from 10.1.2.0/24."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        fabric = spec["sites"]["dc_east"].get("fabric", {})
        vxlan = fabric.get("vxlan", {})
        vtep_sources = vxlan.get("vtep_sources", vxlan.get("vtep_source", ""))
        if isinstance(vtep_sources, dict):
            for dev_name, vtep_ip in vtep_sources.items():
                assert vtep_ip.startswith("10.1.2."), (
                    f"VTEP {dev_name} source {vtep_ip} not in 10.1.2.0/24"
                )
        else:
            assert vtep_sources.startswith("10.1.2."), (
                f"VTEP source {vtep_sources} not in 10.1.2.0/24"
            )


class TestSecurity:
    """Generator must populate security section with FortiGate HA pairs."""

    def test_dc_firewalls_present(self, mock_api: MagicMock) -> None:
        """DC security section must list dc-fw-1 and dc-fw-2."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dc_security = spec.get("security", {}).get("dc", {})
        fw_names = {fw["name"] for fw in dc_security.get("firewalls", [])}
        assert "dc-fw-1" in fw_names
        assert "dc-fw-2" in fw_names

    def test_dr_firewalls_present(self, mock_api: MagicMock) -> None:
        """DR security section must list dr-fw-1 and dr-fw-2."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dr_security = spec.get("security", {}).get("dr", {})
        fw_names = {fw["name"] for fw in dr_security.get("firewalls", [])}
        assert "dr-fw-1" in fw_names
        assert "dr-fw-2" in fw_names


class TestReachabilityMatrix:
    """Generator must build the reachability test matrix from host devices."""

    def test_reachability_matrix_populated(self, mock_api: MagicMock) -> None:
        """The spec must have reachability tests between host devices."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        matrix = spec.get("tests", {}).get("reachability_matrix", [])
        assert len(matrix) > 0, "Reachability matrix must not be empty"

        # Check that all required fields are present
        for entry in matrix:
            assert "source" in entry
            assert "destination" in entry
            assert "description" in entry

    def test_cross_site_reachability_included(self, mock_api: MagicMock) -> None:
        """Matrix must include cross-site tests (DC-to-branch, DC-to-DR)."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        matrix = spec.get("tests", {}).get("reachability_matrix", [])
        sources = {e["source"] for e in matrix}
        destinations = {e["destination"] for e in matrix}
        all_endpoints = sources | destinations

        # Must span at least 2 different sites
        has_dc = any(h.startswith("dc-") for h in all_endpoints)
        has_branch = any(h.startswith("br-") for h in all_endpoints)
        has_dr = any(h.startswith("dr-") for h in all_endpoints)
        assert sum([has_dc, has_branch, has_dr]) >= 2, "Matrix must span multiple sites"


def _build_cloud_mock_api() -> MagicMock:
    """Mock API with the cloud-aws site, aws-vpn-1, and dc-ce-1 carrying Tunnel0+observed.

    Built fresh (does not mutate the module-level ALL_DEVICES) so other tests stay isolated.
    """
    site_cloud = _make_site(4, "Cloud-AWS", "cloud-aws")

    aws_vpn_1 = _make_device(
        22,
        "aws-vpn-1",
        "cloud-aws",
        "cloud-vpn",
        "linux",
        config_context={
            "agent_boundary": "excluded",
            "vpn_tunnels": [
                {
                    "name": "aws-tunnel-1",
                    "tunnel_type": "ipsec",
                    "local_device": "dc-ce-1",
                    "local_interface": "Tunnel0",
                    "local_inner_ip": "169.254.10.1/30",
                    "remote_endpoint": "203.0.113.10",
                    "remote_inner_ip": "169.254.10.2/30",
                    "psk_secret_ref": (
                        "arn:aws:secretsmanager:us-east-1:123456789012:secret:vpn/onprem-psk-AbCdEf"
                    ),
                    "routed_prefixes": ["10.0.64.0/20", "10.0.80.0/20"],
                    "tunnel_source": "GigabitEthernet1",
                }
            ],
        },
    )

    # Fresh dc-ce-1 carrying observed_interfaces (don't mutate the global DC_CE_1)
    dc_ce_1_cloud = _make_device(
        11,
        "dc-ce-1",
        "dc-east",
        "ce",
        "cisco_iosxe",
        config_context={
            "agent_boundary": "excluded",
            "bgp_config": {"asn": 65100},
            "observed_interfaces": ["Tunnel0"],
        },
        custom_fields={"bgp_asn": 65100},
    )

    devices = [d for d in ALL_DEVICES if d.id != 11] + [dc_ce_1_cloud, aws_vpn_1]

    tunnel0 = _make_interface(31, "Tunnel0", "dc-ce-1", 11, description="to-aws-strongswan")
    interfaces = ALL_INTERFACES + [tunnel0]

    api = MagicMock()
    api.dcim.sites.all.return_value = [SITE_DC_EAST, SITE_BRANCH, SITE_DR_WEST, site_cloud]
    api.dcim.devices.all.return_value = devices
    api.dcim.interfaces.all.return_value = interfaces
    api.ipam.ip_addresses.all.return_value = ALL_IPS
    api.ipam.prefixes.all.return_value = ALL_PREFIXES
    api.dcim.cables.all.return_value = ALL_CABLES
    return api


@pytest.fixture
def cloud_mock_api() -> MagicMock:
    """Cloud-aware mock API — extends the base 21-node lab with cloud-aws."""
    return _build_cloud_mock_api()


class TestCloudSiteGeneration:
    """Generator must emit a cloud_aws site with vpn_tunnels and update boundaries."""

    def test_emits_cloud_aws_site(self, cloud_mock_api: MagicMock) -> None:
        """sites.cloud_aws.vpn_tunnels[0].remote_endpoint must match the fixture."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(cloud_mock_api)
        assert "cloud_aws" in spec["sites"]
        tunnels = spec["sites"]["cloud_aws"]["vpn_tunnels"]
        assert len(tunnels) == 1
        assert tunnels[0]["remote_endpoint"] == "203.0.113.10"
        assert tunnels[0]["local_device"] == "dc-ce-1"
        assert tunnels[0]["tunnel_type"] == "ipsec"

    def test_aws_vpn_1_in_excluded_boundary(self, cloud_mock_api: MagicMock) -> None:
        """aws-vpn-1 must land in agent.boundary.excluded."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(cloud_mock_api)
        assert "aws-vpn-1" in spec["agent"]["boundary"]["excluded"]

    def test_dc_ce1_tunnel0_in_observed(self, cloud_mock_api: MagicMock) -> None:
        """dc-ce-1:Tunnel0 must land in observed (even though dc-ce-1 itself is excluded)."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(cloud_mock_api)
        assert "dc-ce-1:Tunnel0" in spec["agent"]["boundary"]["observed"]


class TestGeneratorRendererContract:
    """Generator must produce interface data that the renderer can consume."""

    def test_spine_has_interfaces_with_peers(self, mock_api: MagicMock) -> None:
        """dc-spine-1 must have interfaces with peer info from cables."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dc_devices = spec["sites"]["dc_east"]["devices"]
        spine1 = next(d for d in dc_devices if d["name"] == "dc-spine-1")
        interfaces = spine1.get("interfaces", [])
        assert len(interfaces) > 0, "Spine must have interfaces populated"
        # At least one interface should have a peer from cables
        peers = [i.get("peer") for i in interfaces if "peer" in i]
        assert len(peers) > 0, "Spine interfaces must have peer info from cables"

    def test_interfaces_have_ips(self, mock_api: MagicMock) -> None:
        """Devices with IP-bearing interfaces must have ipv4 in the spec."""
        from generator.netbox_to_spec import generate_spec

        spec = generate_spec(mock_api)
        dc_devices = spec["sites"]["dc_east"]["devices"]
        spine1 = next(d for d in dc_devices if d["name"] == "dc-spine-1")
        interfaces = spine1.get("interfaces", [])
        ips = [i.get("ipv4") for i in interfaces if "ipv4" in i]
        assert len(ips) > 0, "Spine interfaces must have IPs from NetBox"
