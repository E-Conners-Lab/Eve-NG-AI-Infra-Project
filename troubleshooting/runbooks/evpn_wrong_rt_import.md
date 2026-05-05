# Runbook — `evpn-wrong-rt-import`

**Device:** `dc-leaf-2` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** advanced

## Symptom
Asymmetric reachability between dc-host-1 and dc-host-2:

- dc-host-1 → dc-host-2 works (or recovers quickly).
- dc-host-2 → dc-host-1 hits a black hole or floods. Replies don't get
  through reliably.

EVPN sessions are all up. dc-leaf-2 *can see* the Type-2 routes for
dc-host-1 in `show bgp evpn`. They just aren't being installed in the
forwarding tables.

## Hint ladder
1. EVPN routes have to be both *received* and *imported*. Different RIBs.
2. Import is gated by route-target. Export and import RTs are independent settings.
3. If a route is in `show bgp evpn` but not in the L2 RIB, the import
   step is failing.

## Diagnosis

### 1. Confirm the asymmetry

From dc-host-1:

```
dc-host-1$ ping -c 5 10.10.2.10
```

Works. Now from dc-host-2:

```
dc-host-2$ ping -c 5 10.10.1.10
```

Times out or shows packet loss.

### 2. Look at the BGP EVPN table on dc-leaf-2

```
dc-leaf-2# show bgp evpn route-type mac-ip 10.10.1.10
```

The route is **present** — coming from dc-leaf-1's loopback. So the
control plane works.

### 3. Look at the L2 RIB / MAC table on dc-leaf-2

```
dc-leaf-2# show mac address-table dynamic vlan 100
```

The remote MAC for dc-host-1 is **missing**. So the route is in BGP but
not installed.

### 4. Check the EVI route-targets

```
dc-leaf-2# show running-config | section router bgp
```

Look at the `vlan 200` block (or whichever EVI is broken):

```
   vlan 200
      rd 10.1.0.12:10200
      route-target import 65000:99200       <-- WRONG
      route-target export 65000:10200
      redistribute learned
```

The export RT is correct (so dc-leaf-2's outbound advertisements still
look right to the rest of the fabric), but the import RT doesn't match
what dc-leaf-1 is *exporting* (`65000:10200`). Imports without a matching
RT are dropped at the EVI layer — the route stays in BGP RIB but never
makes it to the L2 RIB.

### 5. Compare with dc-leaf-1

```
dc-leaf-1# show running-config | section router bgp
   vlan 200
      route-target import 65000:10200
      route-target export 65000:10200
```

Symmetric on dc-leaf-1; only dc-leaf-2 has drifted.

## Root cause
A typo in the import RT on dc-leaf-2 (`99200` vs `10200`). Probably a
copy-paste or a one-character edit during a config push. The export RT
was untouched, which is why outbound traffic from dc-host-2 still reaches
dc-host-1 — dc-leaf-1's import is correct.

## Fix

```
dc-leaf-2# configure terminal
dc-leaf-2(config)# router bgp 65002
dc-leaf-2(config-router)#  vlan 200
dc-leaf-2(config-router-bgp-vlan-200)#   no route-target import 65000:99200
dc-leaf-2(config-router-bgp-vlan-200)#   route-target import 65000:10200
dc-leaf-2(config-router-bgp-vlan-200)# end
```

dc-leaf-2 will re-evaluate its EVPN imports automatically; remote MACs
populate within seconds.

## Verification

```
dc-leaf-2# show running-config | section router bgp | include route-target
dc-leaf-2# show mac address-table dynamic vlan 100   # remote MACs present
dc-host-2$ ping -c 3 10.10.1.10                       # 100% reachability
```

```
python -m troubleshooting status evpn-wrong-rt-import   # NO FAULT
```

## Why this teaches you something
**Import RT and export RT are independent.** A common mental shortcut is
"route-target both X" — it's syntactic sugar for "set both to the same
value." When you tear those apart explicitly, it's easy to drift one and
not the other, and the symptom is always asymmetric reachability:

- Export RT controls **what others see from you.** Bad export → others
  miss your routes.
- Import RT controls **what you accept from others.** Bad import → you
  miss their routes.

The diagnostic split is: if a route is **in BGP RIB but not in the
forwarding RIB**, the local import is broken. If the route isn't in BGP
RIB at all, the *peer's* export (or your filter / RR config) is broken.
