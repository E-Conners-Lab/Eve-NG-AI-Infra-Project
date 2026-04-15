# Spec Compliance

## Purpose
Compare live device state against the YAML spec and report exact drift.

## Checks (per managed device)
1. **Interface IPs** — Live IPs match spec for every interface
2. **BGP ASN** — Live ASN matches spec
3. **BGP Neighbors** — Live neighbor list matches spec
4. **VNI Mappings** — Live VNI-to-VLAN matches spec (fabric devices)
5. **HA State** — FortiGate HA role matches spec (active/standby)

## Devices
All 15 managed devices (everything except sp-pe-1, sp-pe-2)

## Commands Used
- `show ip interface brief` (EOS/IOS-XE)
- `show bgp summary` (EOS/IOS-XE)
- `show vxlan vni` (EOS)
- `get system ha status` (FortiOS)
- `get system interface` (FortiOS)

## Pass Criteria
Zero drift — every live value matches the spec exactly

## Alert Trigger
Any drift detected. Alert includes:
- Device name
- Field that drifted
- Expected value (from spec)
- Live value (from device)
- Example: "dc-leaf-1: Ethernet2 IP mismatch — spec says 10.1.1.9/31, live shows 10.99.99.1/31"
