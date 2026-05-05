# Runbook — `evpn-rr-peer-missing`

**Device:** `dc-spine-1` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** advanced

## Symptom
Hosts attached to dc-leaf-2 (e.g. dc-host-2) are reachable on the local
leaf — pings within the rack work — but unreachable from anywhere else
in the fabric. dc-leaf-1 and the borders see *no* MAC/IP routes for
dc-host-2 in EVPN. dc-leaf-2 itself receives EVPN routes from the others.

The asymmetry is the giveaway: only dc-leaf-2's *outbound* advertisements
are missing from the rest of the fabric.

## Hint ladder
1. The default view shows the underlay (IPv4 unicast). EVPN is a separate
   address-family with its own activations.
2. RRs forward what they receive. A peer that isn't activated for EVPN
   sends nothing on that AF — and the RR has nothing to reflect.
3. `show ip bgp summary` is the wrong command for an EVPN problem.

## Diagnosis

### 1. Check the underlay (rule it out fast)

```
dc-spine-1# show ip bgp summary
```

All four leaf/border peers Established. Underlay is fine. Move on.

### 2. Check the *EVPN* control plane

```
dc-spine-1# show bgp evpn summary
```

You should see four EVPN sessions (10.1.0.11 through 10.1.0.14). If
`10.1.0.12` is missing from the list — or shows in `Idle`/`NotNeg` — the
spine doesn't have an EVPN session with dc-leaf-2.

### 3. Read what's configured for that neighbor under address-family evpn

```
dc-spine-1# show running-config | section router bgp
```

Look at the `address-family evpn` block:

```
address-family evpn
   neighbor 10.1.0.11 activate
   neighbor 10.1.0.11 next-hop-unchanged
   neighbor 10.1.0.13 activate
   neighbor 10.1.0.13 next-hop-unchanged
   neighbor 10.1.0.14 activate
   neighbor 10.1.0.14 next-hop-unchanged
```

`10.1.0.12` (dc-leaf-2) is missing from this block. The peer is configured
under the global router-bgp section (so the underlay session is up), but
not activated for EVPN. To this spine, dc-leaf-2 is "an underlay-only
peer."

### 4. Confirm from the leaf side

```
dc-leaf-2# show bgp evpn summary
```

dc-leaf-2 may show its EVPN session toward 10.1.0.1 in `Idle` or
`OpenSent`. The reason field will say something like "AFI/SAFI not
negotiated" — the spine refused to bring up the EVPN session because the
neighbor isn't activated locally.

## Root cause
A peer was added to the spine's underlay BGP config but missed in the
`address-family evpn` block (or someone deactivated it manually during a
test and forgot to put it back). The underlay continues to look healthy
because the *unicast* AF is independent of EVPN.

## Fix

```
dc-spine-1# configure terminal
dc-spine-1(config)# router bgp 65000
dc-spine-1(config-router)#  address-family evpn
dc-spine-1(config-router-af)#   neighbor 10.1.0.12 activate
dc-spine-1(config-router-af)#   neighbor 10.1.0.12 next-hop-unchanged
dc-spine-1(config-router-af)# end
```

The session comes up immediately and dc-leaf-2's MAC/IP routes propagate
to the rest of the fabric.

## Verification

```
dc-spine-1# show bgp evpn summary                 # 10.1.0.12 Established
dc-spine-1# show bgp evpn route-type mac-ip vni 10200   # routes from leaf-2 visible
dc-leaf-1# show bgp evpn route-type mac-ip 10.10.2.10   # MAC/IP for dc-host-2
```

```
python -m troubleshooting status evpn-rr-peer-missing   # NO FAULT
```

## Why this teaches you something
**Each address-family is its own fabric.** EVPN, IPv4 unicast, IPv6
unicast — they all ride on the same underlying TCP session, but they are
independently activated per neighbor. A perfectly healthy `show ip bgp
summary` doesn't say anything about EVPN. The right diagnostic order on
an EVPN fabric is:

1. `show ip bgp summary` — underlay reachability between loopbacks.
2. `show bgp evpn summary` — overlay session up?
3. `show bgp evpn route-type mac-ip` — the actual EVPN routes.
4. `show vxlan vni` and `show interfaces Vxlan1` — data plane.

If any layer is broken, everything below it gives misleading results.
