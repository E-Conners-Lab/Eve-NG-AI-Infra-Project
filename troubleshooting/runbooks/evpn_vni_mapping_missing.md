# Runbook — `evpn-vni-mapping-missing`

**Device:** `dc-leaf-2` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** advanced

## Symptom
`dc-host-2` (`10.10.2.10`) is unreachable from `dc-host-1`. The control
plane *looks* healthy: BGP EVPN sessions are up, MAC/IP routes for
10.10.2.10 are visible on the spines and on the remote leaf — but pings
still fail. Local communication on dc-leaf-2's VLAN 200 works fine. It's
only the **inter-VTEP** traffic that's broken.

This is the canonical EVPN trap: the control plane lies to you while the
data plane is silently broken.

## Hint ladder
1. Don't start with BGP. The control plane lies. Start with the data plane.
2. EVPN/VXLAN data plane = Vxlan1 interface, VTEP source, and **VLAN-to-VNI mappings**.
3. If the mapping is missing, the VNI exists for the control plane but not for forwarding.

## Diagnosis

### 1. Confirm the control plane is healthy

```
dc-leaf-2# show bgp evpn
dc-leaf-2# show bgp evpn route-type mac-ip 10.10.2.10
```

You'll see Type-2 routes for 10.10.2.10 — present on both leaves and the
spines. Conclude: *control plane is fine*. This rules out RT/RD mismatches.

### 2. Confirm the underlay

```
dc-leaf-2# show ip bgp summary
dc-leaf-2# ping 10.1.2.3 source loopback1
```

VTEP-to-VTEP underlay reachability via the loopbacks should be green.

### 3. Check the data plane: VLAN-to-VNI mappings on Vxlan1

```
dc-leaf-2# show interfaces Vxlan1
```

Look for the *VNIs* line. You should see both 10100 and 10200. If 10200
is missing, that's the fault.

Confirm with the dedicated command:

```
dc-leaf-2# show vxlan vni
VNI         VLAN       Source       Interface
10100       100        static       Ethernet5
```

(no row for 10200 — matches the symptom)

### 4. Compare with dc-leaf-1

```
dc-leaf-1# show vxlan vni
```

dc-leaf-1 still has both mappings — confirming the asymmetry is a
dc-leaf-2 config issue, not a fabric-wide regression.

### 5. Check the L2VPN EVI / VLAN to confirm the rest of the stack is intact

```
dc-leaf-2# show running-config section vlan
dc-leaf-2# show running-config section interface Vxlan1
```

VLAN 200 should still exist; the VRF and L2 configuration are fine. Only
the `vxlan vlan 200 vni 10200` line is missing from `interface Vxlan1`.

## Root cause
A configuration push or manual edit removed the VLAN-to-VNI mapping on
dc-leaf-2's Vxlan1 interface. Frames from dc-host-2 arrive at Vxlan1 but
have no VNI to encapsulate into, so they're dropped. Frames *to* dc-host-2
arrive encapsulated in VNI 10200, are decapsulated, and have no VLAN to
egress on — also dropped.

## Fix

```
dc-leaf-2# configure terminal
dc-leaf-2(config)# interface Vxlan1
dc-leaf-2(config-if-Vx1)#  vxlan vlan 200 vni 10200
dc-leaf-2(config-if-Vx1)# end
```

No clear / refresh needed — the mapping is purely a forwarding-plane setting.

## Verification

```
dc-leaf-2# show vxlan vni                    # both 10100 and 10200 listed
dc-host-2# ping 10.10.1.10 -c 3              # cross-VNI host reachability works
```

```
python -m troubleshooting status evpn-vni-mapping-missing   # NO FAULT
```

## Why this teaches you something
**Control plane vs. data plane is the most important mental split for
EVPN/VXLAN.** A green BGP table doesn't mean traffic flows. The data plane
is gated by *three* things, all on the local VTEP:

1. The VTEP source loopback is up and reachable in the underlay.
2. The Vxlan1 interface is up.
3. The VLAN ↔ VNI mapping for the affected segment exists.

Senior operators check `show vxlan vni` (or `show interfaces Vxlan1`)
*before* they check BGP. Control-plane diagnostics tell you the route
exists; data-plane diagnostics tell you the route is *useful*.
