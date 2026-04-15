# Fabric Health

## Purpose
Validate the EVPN-VXLAN fabric across DC-East and DR-West sites.

## Checks
1. **eBGP Underlay** — All spine-to-leaf/border sessions must be Established
2. **iBGP EVPN Overlay** — All spine-to-leaf EVPN sessions must be Established
3. **VTEP Reachability** — Ping between all VTEP loopbacks (Loopback1)
4. **VNI-to-VLAN Mappings** — Live mappings match spec
5. **Anycast Gateway** — Same virtual MAC and gateway IP across all leaves per VNI

## Devices
All managed Arista vEOS devices: spines, leaves, border-leaves

## Commands Used
- `show bgp summary` (EOS)
- `show bgp evpn summary` (EOS)
- `show vxlan vtep` (EOS)
- `show vxlan vni` (EOS)
- `show ip virtual-router` (EOS)

## Pass Criteria
- All BGP sessions Established with >0 prefixes received
- All VTEPs reachable via ping
- VNI mappings match spec exactly
- Virtual MAC consistent across all leaves

## Alert Trigger
Any BGP session not Established, any VTEP unreachable, any VNI mismatch
