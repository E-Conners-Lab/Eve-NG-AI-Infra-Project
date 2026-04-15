# NetBox Data Model Reference

This document defines every NetBox object the spec generator (`generator/netbox_to_spec.py`) expects.
Populate NetBox with these objects before running `make generate-spec`.

## Sites

| Name | Slug | Status | Region |
|------|------|--------|--------|
| DC-East | `dc-east` | Active | (optional) |
| Branch-01 | `branch-01` | Active | (optional) |
| DR-West | `dr-west` | Active | (optional) |

## Device Types

| Name | Slug | Manufacturer |
|------|------|-------------|
| vEOS | `veos` | Arista |
| C8000v | `c8000v` | Cisco |
| FortiGate-VM | `fortigate-vm` | Fortinet |
| Alpine Linux | `alpine-linux` | Generic |

## Platforms

| Name | Slug | Manufacturer |
|------|------|-------------|
| Arista EOS | `arista_eos` | Arista |
| Cisco IOS-XE | `cisco_iosxe` | Cisco |
| Fortinet FortiOS | `fortinet_fortios` | Fortinet |
| Linux | `linux` | Generic |

## Device Roles

| Name | Slug | Color | VM Role |
|------|------|-------|---------|
| Spine | `spine` | #4caf50 | No |
| Leaf | `leaf` | #8bc34a | No |
| Border Leaf | `border-leaf` | #ff9800 | No |
| CE Router | `ce` | #2196f3 | No |
| PE Router | `pe` | #9c27b0 | No |
| Firewall | `firewall` | #f44336 | No |
| Host | `host` | #607d8b | No |

## Devices — 21 Nodes

### DC-East (8 devices in site, + 2 firewalls + 3 WAN)

| Name | Role | Device Type | Platform | Site |
|------|------|-------------|----------|------|
| dc-spine-1 | spine | vEOS | arista_eos | dc-east |
| dc-spine-2 | spine | vEOS | arista_eos | dc-east |
| dc-leaf-1 | leaf | vEOS | arista_eos | dc-east |
| dc-leaf-2 | leaf | vEOS | arista_eos | dc-east |
| dc-border-1 | border-leaf | vEOS | arista_eos | dc-east |
| dc-border-2 | border-leaf | vEOS | arista_eos | dc-east |
| dc-host-1 | host | Alpine Linux | linux | dc-east |
| dc-host-2 | host | Alpine Linux | linux | dc-east |
| dc-fw-1 | firewall | FortiGate-VM | fortinet_fortios | dc-east |
| dc-fw-2 | firewall | FortiGate-VM | fortinet_fortios | dc-east |
| dc-ce-1 | ce | C8000v | cisco_iosxe | dc-east |
| sp-pe-1 | pe | C8000v | cisco_iosxe | dc-east |
| sp-pe-2 | pe | C8000v | cisco_iosxe | dc-east |

### Branch-01 (2 devices)

| Name | Role | Device Type | Platform | Site |
|------|------|-------------|----------|------|
| br-ce-1 | ce | C8000v | cisco_iosxe | branch-01 |
| br-host-1 | host | Alpine Linux | linux | branch-01 |

### DR-West (6 devices)

| Name | Role | Device Type | Platform | Site |
|------|------|-------------|----------|------|
| dr-leaf-1 | leaf | vEOS | arista_eos | dr-west |
| dr-leaf-2 | leaf | vEOS | arista_eos | dr-west |
| dr-fw-1 | firewall | FortiGate-VM | fortinet_fortios | dr-west |
| dr-fw-2 | firewall | FortiGate-VM | fortinet_fortios | dr-west |
| dr-ce-1 | ce | C8000v | cisco_iosxe | dr-west |
| dr-host-1 | host | Alpine Linux | linux | dr-west |

## Interfaces

### Naming Conventions per Vendor

| Vendor | Loopback | Physical | Management |
|--------|----------|----------|------------|
| Arista vEOS | Loopback0, Loopback1 | Ethernet1, Ethernet2, ... | Management1 |
| Cisco C8000v | Loopback0 | GigabitEthernet1, GigabitEthernet2, ... | GigabitEthernet1 |
| FortiGate-VM | — | port1, port2, port3, ... | port1 |
| Alpine Linux | — | eth0 | eth0 |

### Key Interfaces per Device

| Device | Interface | Description | Connected To |
|--------|-----------|-------------|-------------|
| dc-spine-1 | Loopback0 | Router ID | — |
| dc-spine-1 | Ethernet1 | to dc-leaf-1 | dc-leaf-1 Ethernet1 |
| dc-spine-1 | Ethernet2 | to dc-leaf-2 | dc-leaf-2 Ethernet1 |
| dc-spine-1 | Ethernet3 | to dc-border-1 | dc-border-1 Ethernet1 |
| dc-spine-1 | Ethernet4 | to dc-border-2 | dc-border-2 Ethernet1 |
| dc-spine-2 | Loopback0 | Router ID | — |
| dc-spine-2 | Ethernet1 | to dc-leaf-1 | dc-leaf-1 Ethernet2 |
| dc-spine-2 | Ethernet2 | to dc-leaf-2 | dc-leaf-2 Ethernet2 |
| dc-spine-2 | Ethernet3 | to dc-border-1 | dc-border-1 Ethernet2 |
| dc-spine-2 | Ethernet4 | to dc-border-2 | dc-border-2 Ethernet2 |
| dc-leaf-1 | Loopback0 | Router ID | — |
| dc-leaf-1 | Loopback1 | VTEP source | — |
| dc-leaf-1 | Ethernet1 | to dc-spine-1 | dc-spine-1 Ethernet1 |
| dc-leaf-1 | Ethernet2 | to dc-spine-2 | dc-spine-2 Ethernet1 |
| dc-leaf-1 | Ethernet3 | to dc-host-1 | dc-host-1 eth0 |
| dc-leaf-2 | Loopback0 | Router ID | — |
| dc-leaf-2 | Loopback1 | VTEP source | — |
| dc-leaf-2 | Ethernet1 | to dc-spine-1 | dc-spine-1 Ethernet2 |
| dc-leaf-2 | Ethernet2 | to dc-spine-2 | dc-spine-2 Ethernet2 |
| dc-leaf-2 | Ethernet3 | to dc-host-2 | dc-host-2 eth0 |
| dc-border-1 | Loopback0 | Router ID | — |
| dc-border-1 | Ethernet1 | to dc-spine-1 | dc-spine-1 Ethernet3 |
| dc-border-1 | Ethernet2 | to dc-spine-2 | dc-spine-2 Ethernet3 |
| dc-border-1 | Ethernet3 | to dc-fw-1 | dc-fw-1 port1 |
| dc-border-2 | Loopback0 | Router ID | — |
| dc-border-2 | Ethernet1 | to dc-spine-1 | dc-spine-1 Ethernet4 |
| dc-border-2 | Ethernet2 | to dc-spine-2 | dc-spine-2 Ethernet4 |
| dc-border-2 | Ethernet3 | to dc-fw-2 | dc-fw-2 port1 |
| dc-fw-1 | port1 | to dc-border-1 | dc-border-1 Ethernet3 |
| dc-fw-1 | port2 | to dc-ce-1 | dc-ce-1 GigabitEthernet2 |
| dc-fw-2 | port1 | to dc-border-2 | dc-border-2 Ethernet3 |
| dc-fw-2 | port2 | to dc-ce-1 | dc-ce-1 GigabitEthernet3 |
| dc-ce-1 | Loopback0 | Router ID | — |
| dc-ce-1 | GigabitEthernet1 | to sp-pe-1 | sp-pe-1 GigabitEthernet1 |
| dc-ce-1 | GigabitEthernet2 | to dc-fw-1 | dc-fw-1 port2 |
| dc-ce-1 | GigabitEthernet3 | to dc-fw-2 | dc-fw-2 port2 |
| dc-ce-1 | GigabitEthernet4 | to sp-pe-2 | sp-pe-2 GigabitEthernet1 |
| sp-pe-1 | Loopback0 | Router ID | — |
| sp-pe-1 | GigabitEthernet1 | to dc-ce-1 | dc-ce-1 GigabitEthernet1 |
| sp-pe-1 | GigabitEthernet2 | to sp-pe-2 | sp-pe-2 GigabitEthernet2 |
| sp-pe-1 | GigabitEthernet3 | to br-ce-1 | br-ce-1 GigabitEthernet1 |
| sp-pe-1 | GigabitEthernet4 | to dr-ce-1 | dr-ce-1 GigabitEthernet1 |
| sp-pe-2 | Loopback0 | Router ID | — |
| sp-pe-2 | GigabitEthernet1 | to dc-ce-1 | dc-ce-1 GigabitEthernet4 |
| sp-pe-2 | GigabitEthernet2 | to sp-pe-1 | sp-pe-1 GigabitEthernet2 |
| sp-pe-2 | GigabitEthernet3 | to br-ce-1 | br-ce-1 GigabitEthernet2 |
| sp-pe-2 | GigabitEthernet4 | to dr-ce-1 | dr-ce-1 GigabitEthernet2 |
| br-ce-1 | Loopback0 | Router ID | — |
| br-ce-1 | GigabitEthernet1 | to sp-pe-1 | sp-pe-1 GigabitEthernet3 |
| br-ce-1 | GigabitEthernet2 | to sp-pe-2 | sp-pe-2 GigabitEthernet3 |
| br-ce-1 | GigabitEthernet3 | to br-host-1 | br-host-1 eth0 |
| dr-leaf-1 | Loopback0 | Router ID | — |
| dr-leaf-1 | Ethernet1 | to dr-leaf-2 | dr-leaf-2 Ethernet1 |
| dr-leaf-1 | Ethernet2 | to dr-fw-1 | dr-fw-1 port1 |
| dr-leaf-2 | Loopback0 | Router ID | — |
| dr-leaf-2 | Ethernet1 | to dr-leaf-1 | dr-leaf-1 Ethernet1 |
| dr-leaf-2 | Ethernet2 | to dr-fw-2 | dr-fw-2 port1 |
| dr-fw-1 | port1 | to dr-leaf-1 | dr-leaf-1 Ethernet2 |
| dr-fw-1 | port2 | to dr-ce-1 | dr-ce-1 GigabitEthernet2 |
| dr-fw-2 | port1 | to dr-leaf-2 | dr-leaf-2 Ethernet2 |
| dr-fw-2 | port2 | to dr-ce-1 | dr-ce-1 GigabitEthernet3 |
| dr-ce-1 | Loopback0 | Router ID | — |
| dr-ce-1 | GigabitEthernet1 | to sp-pe-1 | sp-pe-1 GigabitEthernet4 |
| dr-ce-1 | GigabitEthernet2 | to dr-fw-1 | dr-fw-1 port2 |
| dr-ce-1 | GigabitEthernet3 | to dr-fw-2 | dr-fw-2 port2 |
| dr-ce-1 | GigabitEthernet4 | to sp-pe-2 | sp-pe-2 GigabitEthernet4 |

## IP Addresses

### Addressing Plan

| Segment | Prefix | Description | Site |
|---------|--------|-------------|------|
| DC underlay loopbacks | 10.1.0.0/24 | Spine/leaf Loopback0 | dc-east |
| DC underlay P2P | 10.1.1.0/24 | /31 pairs between spine-leaf | dc-east |
| DC VTEP loopbacks | 10.1.2.0/24 | Loopback1 for VTEP source | dc-east |
| DC overlay VNI 10100 | 10.10.1.0/24 | SERVERS_A subnet | dc-east |
| DC overlay VNI 10200 | 10.10.2.0/24 | SERVERS_B subnet | dc-east |
| Border-to-FW transit | 10.99.0.0/24 | dc-border to dc-fw links | dc-east |
| FW-to-CE transit | 10.99.1.0/24 | dc-fw to dc-ce-1 links | dc-east |
| SP transport | 172.16.0.0/24 | /31 pairs between PE/CE | (global) |
| Branch | 10.20.0.0/16 | All branch addressing | branch-01 |
| DR overlay | 10.30.0.0/16 | DR host subnets | dr-west |
| DR underlay | 10.31.0.0/24 | DR leaf loopbacks | dr-west |

### Loopback Assignments

| Device | Interface | IP Address |
|--------|-----------|------------|
| dc-spine-1 | Loopback0 | 10.1.0.1/32 |
| dc-spine-2 | Loopback0 | 10.1.0.2/32 |
| dc-leaf-1 | Loopback0 | 10.1.0.11/32 |
| dc-leaf-1 | Loopback1 | 10.1.2.11/32 |
| dc-leaf-2 | Loopback0 | 10.1.0.12/32 |
| dc-leaf-2 | Loopback1 | 10.1.2.12/32 |
| dc-border-1 | Loopback0 | 10.1.0.13/32 |
| dc-border-2 | Loopback0 | 10.1.0.14/32 |
| dc-ce-1 | Loopback0 | 172.16.0.1/32 |
| sp-pe-1 | Loopback0 | 172.16.0.11/32 |
| sp-pe-2 | Loopback0 | 172.16.0.12/32 |
| br-ce-1 | Loopback0 | 10.20.0.1/32 |
| dr-leaf-1 | Loopback0 | 10.31.0.1/32 |
| dr-leaf-2 | Loopback0 | 10.31.0.2/32 |

### Host Addresses

| Device | Interface | IP Address |
|--------|-----------|------------|
| dc-host-1 | eth0 | 10.10.1.10/24 |
| dc-host-2 | eth0 | 10.10.2.10/24 |
| br-host-1 | eth0 | 10.20.1.10/24 |
| dr-host-1 | eth0 | 10.30.1.10/24 |

## Prefixes

Create these in IPAM > Prefixes. Assign each to its site where indicated.

| Prefix | Description | Site | Status |
|--------|-------------|------|--------|
| 10.1.0.0/24 | DC underlay loopbacks | dc-east | Active |
| 10.1.1.0/24 | DC underlay P2P | dc-east | Active |
| 10.1.2.0/24 | DC VTEP loopbacks | dc-east | Active |
| 10.10.1.0/24 | DC overlay VNI 10100 | dc-east | Active |
| 10.10.2.0/24 | DC overlay VNI 10200 | dc-east | Active |
| 10.99.0.0/24 | Border-to-FW transit | dc-east | Active |
| 10.99.1.0/24 | FW-to-CE transit | dc-east | Active |
| 172.16.0.0/24 | SP transport | (none) | Active |
| 10.20.0.0/16 | Branch addressing | branch-01 | Active |
| 10.30.0.0/16 | DR overlay | dr-west | Active |
| 10.31.0.0/24 | DR underlay | dr-west | Active |

## Cables

Create these in DCIM > Cables. Each cable connects two device interfaces.

| # | A Device | A Interface | Z Device | Z Interface |
|---|----------|-------------|----------|-------------|
| 1 | dc-spine-1 | Ethernet1 | dc-leaf-1 | Ethernet1 |
| 2 | dc-spine-1 | Ethernet2 | dc-leaf-2 | Ethernet1 |
| 3 | dc-spine-1 | Ethernet3 | dc-border-1 | Ethernet1 |
| 4 | dc-spine-1 | Ethernet4 | dc-border-2 | Ethernet1 |
| 5 | dc-spine-2 | Ethernet1 | dc-leaf-1 | Ethernet2 |
| 6 | dc-spine-2 | Ethernet2 | dc-leaf-2 | Ethernet2 |
| 7 | dc-spine-2 | Ethernet3 | dc-border-1 | Ethernet2 |
| 8 | dc-spine-2 | Ethernet4 | dc-border-2 | Ethernet2 |
| 9 | dc-leaf-1 | Ethernet3 | dc-host-1 | eth0 |
| 10 | dc-leaf-2 | Ethernet3 | dc-host-2 | eth0 |
| 11 | dc-border-1 | Ethernet3 | dc-fw-1 | port1 |
| 12 | dc-border-2 | Ethernet3 | dc-fw-2 | port1 |
| 13 | dc-fw-1 | port2 | dc-ce-1 | GigabitEthernet2 |
| 14 | dc-fw-2 | port2 | dc-ce-1 | GigabitEthernet3 |
| 15 | dc-ce-1 | GigabitEthernet1 | sp-pe-1 | GigabitEthernet1 |
| 16 | dc-ce-1 | GigabitEthernet4 | sp-pe-2 | GigabitEthernet1 |
| 17 | sp-pe-1 | GigabitEthernet2 | sp-pe-2 | GigabitEthernet2 |
| 18 | sp-pe-1 | GigabitEthernet3 | br-ce-1 | GigabitEthernet1 |
| 19 | sp-pe-2 | GigabitEthernet3 | br-ce-1 | GigabitEthernet2 |
| 20 | sp-pe-1 | GigabitEthernet4 | dr-ce-1 | GigabitEthernet1 |
| 21 | sp-pe-2 | GigabitEthernet4 | dr-ce-1 | GigabitEthernet4 |
| 22 | br-ce-1 | GigabitEthernet3 | br-host-1 | eth0 |
| 23 | dr-leaf-1 | Ethernet1 | dr-leaf-2 | Ethernet1 |
| 24 | dr-leaf-1 | Ethernet2 | dr-fw-1 | port1 |
| 25 | dr-leaf-2 | Ethernet2 | dr-fw-2 | port1 |
| 26 | dr-fw-1 | port2 | dr-ce-1 | GigabitEthernet2 |
| 27 | dr-fw-2 | port2 | dr-ce-1 | GigabitEthernet3 |
| 28 | dr-ce-1 | GigabitEthernet1 | sp-pe-1 | GigabitEthernet4 |
| 29 | dr-ce-1 | GigabitEthernet4 | sp-pe-2 | GigabitEthernet4 |

## Config Contexts

Config contexts carry per-device metadata that the generator uses for agent boundary classification, BGP configuration, VXLAN settings, and HA pairing. Create these as JSON config contexts assigned to devices.

### agent_boundary

Assigned to all network devices (not hosts). Determines agent classification.

```json
{
  "agent_boundary": "managed"
}
```

Values: `"managed"`, `"observed"`, or `"excluded"`.

| Device | agent_boundary |
|--------|---------------|
| dc-spine-1, dc-spine-2 | managed |
| dc-leaf-1, dc-leaf-2 | managed |
| dc-border-1, dc-border-2 | managed |
| br-ce-1 | managed |
| dr-leaf-1, dr-leaf-2 | managed |
| dc-fw-1, dc-fw-2, dr-fw-1, dr-fw-2 | excluded |
| dc-ce-1, dr-ce-1, sp-pe-1, sp-pe-2 | excluded |

### bgp_config

Assigned to all devices running BGP. Provides ASN and EVPN role.

```json
{
  "bgp_config": {
    "asn": 65000,
    "role": "rr"
  }
}
```

| Device | ASN | Role |
|--------|-----|------|
| dc-spine-1, dc-spine-2 | 65000 | rr |
| dc-leaf-1 | 65001 | client |
| dc-leaf-2 | 65002 | client |
| dc-border-1 | 65003 | — |
| dc-border-2 | 65004 | — |
| br-ce-1 | 65100 | — |
| dc-ce-1 | 65100 | — |
| sp-pe-1, sp-pe-2 | 64500 | — |
| dr-leaf-1 | 65201 | client |
| dr-leaf-2 | 65202 | client |
| dr-ce-1 | 65100 | — |

### vxlan_config

Assigned to VTEP devices (dc-leaf-1, dc-leaf-2).

```json
{
  "vxlan_config": {
    "vtep_source_interface": "Loopback1",
    "vni_mappings": [
      {
        "vni": 10100,
        "vlan": 100,
        "name": "SERVERS_A",
        "subnet": "10.10.1.0/24"
      }
    ]
  }
}
```

| Device | VTEP Source | VNI | VLAN | Name | Subnet |
|--------|-----------|-----|------|------|--------|
| dc-leaf-1 | Loopback1 | 10100 | 100 | SERVERS_A | 10.10.1.0/24 |
| dc-leaf-2 | Loopback1 | 10200 | 200 | SERVERS_B | 10.10.2.0/24 |

### ha_config

Assigned to FortiGate firewall pairs.

```json
{
  "ha_config": {
    "role": "active",
    "peer": "dc-fw-2"
  }
}
```

| Device | Role | Peer |
|--------|------|------|
| dc-fw-1 | active | dc-fw-2 |
| dc-fw-2 | standby | dc-fw-1 |
| dr-fw-1 | active | dr-fw-2 |
| dr-fw-2 | standby | dr-fw-1 |

## Custom Fields (Alternative to Config Contexts)

If you prefer custom fields over config contexts for BGP/VXLAN data:

| Field Name | Type | Description | Applied To |
|-----------|------|-------------|-----------|
| bgp_asn | Integer | BGP Autonomous System Number | Devices |
| evpn_role | Selection (rr/client/none) | EVPN role in the overlay | Devices |
| vtep_source_interface | Text | Interface used as VTEP source | Devices |
| vni_mappings | JSON | VNI-to-VLAN mapping array | Devices |

The generator checks config contexts first, then falls back to custom fields.

## NetBox Population Checklist

Follow this order when populating NetBox from scratch for this lab:

1. **Manufacturers** — Create: Arista, Cisco, Fortinet, Generic
2. **Platforms** — Create: arista_eos, cisco_iosxe, fortinet_fortios, linux (with manufacturer links)
3. **Device Types** — Create: vEOS, C8000v, FortiGate-VM, Alpine Linux
4. **Device Roles** — Create: spine, leaf, border-leaf, ce, pe, firewall, host
5. **Sites** — Create: dc-east, branch-01, dr-west (all Active)
6. **Devices** — Create all 21 devices with correct site, role, type, platform assignments
7. **Interfaces** — Create all interfaces per device (follow naming conventions above)
8. **Prefixes** — Create all 11 prefixes in IPAM with site assignments
9. **IP Addresses** — Create all IP addresses and assign to interfaces
10. **Cables** — Create all 29 cables connecting device interfaces
11. **Config Contexts** — Create and assign:
    - `agent_boundary` to all network devices
    - `bgp_config` to all BGP-speaking devices
    - `vxlan_config` to VTEP devices (dc-leaf-1, dc-leaf-2)
    - `ha_config` to firewall pairs
12. **Verify** — Run `make generate-spec` and confirm 21 devices across 3 sites
