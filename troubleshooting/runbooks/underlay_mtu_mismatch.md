# Runbook — `underlay-mtu-mismatch`

**Device:** `dc-leaf-1` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** advanced

## Symptom
Small pings work everywhere. Application traffic and large pings between
dc-host-1 and dc-host-2 hang or retransmit constantly. BGP and EVPN
sessions are all Established and stable. SSH into any device works fine.
Only "real-sized" data plane traffic is broken.

## Hint ladder
1. Control-plane green + data-plane red is almost always one of: ACL,
   forwarding-table miss, or MTU.
2. If small packets pass and large ones don't, it's MTU. Always.
3. VXLAN adds ~50 bytes of outer header. The underlay MTU has to absorb that.

## Diagnosis

### 1. Verify the symptom shape

```
dc-host-1$ ping -c 3 -s 56 -M do 10.10.2.10        # works
dc-host-1$ ping -c 3 -s 1450 -M do 10.10.2.10      # silent black hole
```

`-M do` sets DF, so a misbehaving box can't fragment around the problem.
This is the cleanest "it's MTU" signal you can produce.

### 2. Confirm the control plane is fine

```
dc-leaf-1# show ip bgp summary       # all up
dc-leaf-1# show bgp evpn summary     # all up
dc-leaf-1# show vxlan vni            # mappings present
```

Rules out the obvious EVPN/VXLAN problems.

### 3. Walk MTU on each underlay interface

```
dc-leaf-1# show interfaces Ethernet1
...
  IP MTU 1400 bytes
```

That's the smoking gun. The link to dc-spine-1 only allows 1400-byte IP
payloads. With a 1500-byte original payload + ~50 bytes VXLAN outer +
20 bytes IPv4 outer header, encapped frames are around 1570 — far over
1400, dropped.

Check the other uplink for comparison:

```
dc-leaf-1# show interfaces Ethernet2 | include MTU
  IP MTU 9214 bytes
```

Et2 is correct. Et1 is wrong. Asymmetry confirmed.

### 4. (Optional) Verify the spine side matches

```
dc-spine-1# show interfaces Ethernet1 | include MTU
  IP MTU 9214 bytes
```

The spine side is jumbo. The cap is at the leaf side.

## Root cause
A 1400-byte MTU was set on Ethernet1 of dc-leaf-1 — possibly a leftover
from a tunnel-MTU experiment, or a paste from another platform's config
that uses 1400 as a default. Underlay BGP keepalives are 19–45 bytes;
they sail through. EVPN UPDATEs are usually under 4 KB but get fragmented
by IP if necessary, so the control plane stays green. Only fully-sized
data-plane frames pay the price.

## Fix

```
dc-leaf-1# configure terminal
dc-leaf-1(config)# interface Ethernet1
dc-leaf-1(config-if-Et1)#  mtu 9214
dc-leaf-1(config-if-Et1)# end
```

The change is hitless — no flap, no session reset.

## Verification

```
dc-leaf-1# show interfaces Ethernet1 | include MTU       # 9214
dc-host-1$ ping -c 3 -s 1450 -M do 10.10.2.10            # works
```

```
python -m troubleshooting status underlay-mtu-mismatch    # NO FAULT
```

## Why this teaches you something
**MTU is the thing the control plane will never tell you about.** BGP
keepalives are tiny; OSPF hellos are tiny; LLDP frames are tiny. The
moment you put a real payload on the wire, you find out about MTU
problems — usually as TCP retransmits or "the cluster is just slow today."

The underlay MTU rule for VXLAN is simple: **set it to jumbo (9214 or
9000) everywhere, end-to-end, and never trust a config that doesn't.**
A `show interfaces | include MTU` sweep of the underlay path is part of
every EVPN deployment runbook for exactly this reason.
