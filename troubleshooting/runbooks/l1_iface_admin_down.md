# Runbook — `l1-iface-admin-down`

**Device:** `dc-border-1` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** beginner

## Symptom
One of dc-border-1's two BGP sessions to the spines is down. North-south
traffic still flows but ECMP load-sharing is gone.

## Hint ladder
1. Look at the BGP summary first — which neighbor is down, and what state?
2. The neighbor's IP tells you which **interface** carries that session.
3. Don't trust `show ip route` for L1 problems — go to the interface itself.

## Diagnosis

### 1. See which BGP session is broken

```
dc-border-1# show ip bgp summary
```

Expect one neighbor in `Idle` or `Active` (the other Established).

### 2. Map the failed neighbor IP to a local interface

`10.1.1.4/31` is the spine-side address of the Et1 link
(see `specs/generated/lab_spec.yaml` — dc-border-1 Ethernet1 is `10.1.1.5/31`).

```
dc-border-1# show ip route 10.1.1.4
```

Should be `directly connected, Ethernet1`. Or:

```
dc-border-1# show ip interface brief | include 10.1.1
```

### 3. Confirm the interface is the cause

```
dc-border-1# show interfaces Ethernet1 status
Port       Name              Status       Vlan       Duplex  Speed   Type
Et1        to dc-spine-1     disabled     routed     ...
```

`disabled` (Arista) = administratively shut. Compare with the peer link:

```
dc-border-1# show interfaces Ethernet2 status
```

Et2 should show `connected`.

## Root cause
Someone ran `interface Ethernet1` / `shutdown` in config mode and forgot to
back it out. Common in change windows.

## Fix

```
dc-border-1# configure terminal
dc-border-1(config)# interface Ethernet1
dc-border-1(config-if-Et1)# no shutdown
dc-border-1(config-if-Et1)# end
```

## Verification

```
dc-border-1# show interfaces Ethernet1 status         # connected
dc-border-1# show ip bgp summary                      # both neighbors Established
```

End-state check from the framework:

```
python -m troubleshooting status l1-iface-admin-down  # NO FAULT
```

## Why this teaches you something
Half of real outages start at L1. The senior-engineer move is *not* to
chase the BGP state — it's to map the broken adjacency back to a physical
(or virtual) interface in one hop. `show ip bgp summary` tells you which
neighbor is broken; `show ip route <neighbor-ip>` tells you which interface
that neighbor lives behind.
