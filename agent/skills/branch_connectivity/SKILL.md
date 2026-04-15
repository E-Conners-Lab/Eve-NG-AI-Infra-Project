# Branch Connectivity

## Purpose
Validate branch site dual-homed WAN connectivity to both PE routers.

## Checks
1. **eBGP to PE-1** — br-ce-1 session to sp-pe-1 must be Established
2. **eBGP to PE-2** — br-ce-1 session to sp-pe-2 must be Established
3. **Route Advertisement** — Branch prefixes (10.20.0.0/16) advertised toward WAN
4. **Route Reception** — DC and DR prefixes received from WAN
5. **Path Reachability** — Ping from branch to DC and DR prefixes

## Devices
br-ce-1 (managed), dc-ce-1 (managed), dr-ce-1 (managed)

## Commands Used
- `show bgp summary` (IOS-XE)
- `show bgp ipv4 unicast` (IOS-XE)
- `ping` (from br-ce-1 to DC/DR loopbacks)

## Pass Criteria
- Both PE sessions Established (dual-homed)
- Branch prefixes present in BGP table
- DC/DR prefixes reachable from branch

## Alert Trigger
Either PE session down (single-homed = risk), missing routes, ping failure
