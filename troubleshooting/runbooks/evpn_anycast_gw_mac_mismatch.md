# Runbook — `evpn-anycast-gw-mac-mismatch`

**Device:** `dc-leaf-1` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** advanced

## Symptom
Hosts work most of the time. After a vMotion, a reboot, or anything that
flushes ARP, the host can't reach its default gateway for several minutes
— then it spontaneously recovers. dc-leaf-1's hosts and dc-leaf-2's hosts
behave inconsistently. Sometimes only a *subset* of new flows works.

This is the trademark behaviour of inconsistent gateway state, not link
state. The fabric is healthy; the gateway abstraction is leaking.

## Hint ladder
1. Anycast means "same address, multiple devices." For it to work, the
   *MAC* the hosts see has to be the same on every leaf.
2. Where is the anycast MAC defined? It's a global setting per leaf.
3. Don't trust the running-config alone — `show ip virtual-router` is the
   clean view.

## Diagnosis

### 1. Confirm the symptom shape

ARP from a host (dc-host-1) for its gateway (10.10.1.1):

```
dc-host-1$ arp -n 10.10.1.1
```

Note the MAC. Now ask a host on the *other* leaf the same question:

```
dc-host-2$ arp -n 10.10.2.1
```

If the two MACs are **different**, that's the bug. They should be
identical (both showing the fabric anycast MAC).

### 2. Check anycast MAC on each leaf

```
dc-leaf-1# show ip virtual-router
Anycast MAC address: 00:1c:73:de:ad:01

dc-leaf-2# show ip virtual-router
Anycast MAC address: 00:1c:73:00:00:01
```

Two different values — confirmed.

### 3. Read the running config

```
dc-leaf-1# show running-config | include ip virtual-router
ip virtual-router mac-address 00:1c:73:de:ad:01

dc-leaf-2# show running-config | include ip virtual-router
ip virtual-router mac-address 00:1c:73:00:00:01
```

The fabric expects `00:1c:73:00:00:01` everywhere. dc-leaf-1 was set to
something else.

### 4. Check the L2 RIB on the spine to see how the conflict shows up

```
dc-spine-1# show bgp evpn route-type mac-ip
```

You'll see Type-2 routes for both gateway MACs being advertised — and
hosts learn whichever they ARP'd first. Anycast is supposed to make this
question go away; with mismatched MACs, the question is back.

## Root cause
A leaf's `ip virtual-router mac-address` was changed (probably during a
config audit or copy from another lab) and didn't match the fabric value
the other leaves use. Hosts behind that leaf cache the wrong MAC. After
moves or ARP expiry, the wrong MAC is still cached on remote ARP tables
until those expire — leading to drops or asymmetric forwarding.

## Fix

```
dc-leaf-1# configure terminal
dc-leaf-1(config)# ip virtual-router mac-address 00:1c:73:00:00:01
dc-leaf-1(config)# end
```

Hosts behind dc-leaf-1 will need to refresh their ARP — easiest way is to
ping the gateway from a host (forces a fresh ARP) and the cluster
recovers. Or wait for the ARP timeout (~4 hours by default).

## Verification

```
dc-leaf-1# show ip virtual-router       # MAC matches fabric value
dc-leaf-2# show ip virtual-router       # same MAC
dc-host-1$ arp -d 10.10.1.1; ping -c 1 10.10.1.1
dc-host-1$ arp -n 10.10.1.1             # matches dc-host-2's view
```

```
python -m troubleshooting status evpn-anycast-gw-mac-mismatch   # NO FAULT
```

## Why this teaches you something
**Anycast only works when every endpoint actually behaves identically.**
The fabric design says "any leaf is the gateway for these subnets" — but
the clients don't talk to "the gateway," they talk to a *specific MAC*.
If two leaves disagree about that MAC, the abstraction breaks for hosts
that move or refresh.

The diagnostic instinct here is: when symptoms are intermittent and tied
to host moves or ARP timing, suspect a state inconsistency between the
boxes that *should* be acting identically. Spread `show ip virtual-router`
(or its vendor equivalent) across all participating leaves and compare.
