# Runbook — `wan-prefix-filter-typo`

**Device:** `dc-ce-1` &nbsp;|&nbsp; **Platform:** Cisco IOS-XE &nbsp;|&nbsp; **Difficulty:** intermediate

## Symptom
Branch prefixes (`10.20.0.0/16`) are still reachable from DC, but the path
is going via **sp-pe-2** (secondary) instead of sp-pe-1 (primary). Both
BGP sessions are Established. Local-pref policy looks correct in the
running-config. So why is the wrong PE winning?

## Hint ladder
1. `show ip bgp summary` will show similar but not identical prefix counts on the two neighbors. Compare them.
2. If a path doesn't appear in `show ip bgp <prefix>`, it was either never sent or filtered on receive.
3. Inbound policy on a neighbor isn't only about route-maps. What other knobs filter prefixes?

## Diagnosis

### 1. Confirm the missing path

```
dc-ce-1# show ip bgp 10.20.0.1
```

You'll see only **one** path, via 172.16.0.3. Healthy state has two paths
(one per PE).

### 2. Compare prefix counts on the two neighbors

```
dc-ce-1# show ip bgp summary
```

Look at `PfxRcd` — the secondary PE will receive one more prefix than the
primary. That delta is your filtered prefix.

### 3. Check what was actually sent vs. what was accepted

```
dc-ce-1# show ip bgp neighbors 172.16.0.1 received-routes
dc-ce-1# show ip bgp neighbors 172.16.0.1 routes
```

(`received-routes` requires `soft-reconfiguration inbound` enabled — if
not configured you'll get an error. In that case, jump to step 4.)

If `received-routes` shows `10.20.0.1/32` but `routes` (the accepted set)
doesn't, the path arrived and was *then* dropped — confirming an inbound
filter.

### 4. Read the inbound policy on the primary neighbor

```
dc-ce-1# show running-config | section router bgp
```

Look at all `neighbor 172.16.0.1 ... in` directives. You'll see
the expected route-map (`PRIMARY-PE`), but also a stray:

```
neighbor 172.16.0.1 prefix-list TS-DENY-BRANCH in
```

Now read what that prefix-list does:

```
dc-ce-1# show ip prefix-list TS-DENY-BRANCH
```

```
seq 10 deny 10.20.0.0/16 le 32
seq 20 permit 0.0.0.0/0 le 32
```

That seq 10 is dropping every more-specific from the branch supernet —
including the `/32` loopback you tried to find.

## Root cause
A "temporary" prefix-list left over from a maintenance window — probably
created to filter a single route during a migration, then bound to the
neighbor and forgotten when the change was complete. The route-map for
local-pref is doing the right thing on whatever's left, but the prefix-list
runs first.

## Fix
Remove the inbound binding and delete the orphaned prefix-list:

```
dc-ce-1# configure terminal
dc-ce-1(config)# router bgp 65100
dc-ce-1(config-router)#  no neighbor 172.16.0.1 prefix-list TS-DENY-BRANCH in
dc-ce-1(config-router)# exit
dc-ce-1(config)# no ip prefix-list TS-DENY-BRANCH
dc-ce-1(config)# end
dc-ce-1# clear ip bgp 172.16.0.1 soft in
```

`soft in` re-pulls the previously-filtered prefixes without resetting the
session.

## Verification

```
dc-ce-1# show ip bgp 10.20.0.1     # two paths, best via 172.16.0.1
dc-ce-1# show ip bgp summary       # PfxRcd matches between neighbors
```

```
python -m troubleshooting status wan-prefix-filter-typo   # NO FAULT
```

## Why this teaches you something
**Multiple inbound filters compose.** A single neighbor can have a
prefix-list, a route-map, an as-path filter, and a distribute-list — all
applied in a fixed order. `show ip bgp summary` won't reveal which one
dropped a prefix; you have to enumerate the bindings and read each filter
body. The `received-routes` vs `routes` comparison is the cleanest
diagnostic when soft-reconfig is on; the prefix-count delta is the cleanest
proxy when it isn't.
