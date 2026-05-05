# Runbook — `wan-localpref-reversed`

**Device:** `dc-ce-1` &nbsp;|&nbsp; **Platform:** Cisco IOS-XE &nbsp;|&nbsp; **Difficulty:** intermediate

## Symptom
DC traffic toward the branch and DR sites is leaving via **sp-pe-2** instead
of **sp-pe-1**. BGP is Established to both PEs, all prefixes are present.
The "primary" path is no longer primary.

## Hint ladder
1. The neighbor bindings still mention `PRIMARY-PE` and `SECONDARY-PE`. Don't trust the names — read the bodies.
2. `show ip bgp summary` won't tell you which path won; you need `show ip bgp <prefix>`.
3. The fault is *not* in the neighbor statements. It's one config block higher.

## Diagnosis

### 1. Confirm the wrong PE is winning best-path

```
dc-ce-1# show ip bgp 10.20.0.1
```

You'll see two paths. Note which one is marked `best` and what the
`localpref` is on each. Expected: best path next-hop = `172.16.0.1` (sp-pe-1),
localpref 200. Bad state: best is `172.16.0.3` (sp-pe-2), localpref 200.

### 2. Find the policy that *should* be elevating sp-pe-1

```
dc-ce-1# show running-config | section router bgp
```

Look at the inbound route-map bindings:

```
neighbor 172.16.0.1 route-map PRIMARY-PE in
neighbor 172.16.0.3 route-map SECONDARY-PE in
```

Names look right. Now read what those route-maps actually do:

```
dc-ce-1# show route-map PRIMARY-PE
dc-ce-1# show route-map SECONDARY-PE
```

If `PRIMARY-PE` is setting local-preference **100** and `SECONDARY-PE` is
setting **200**, the *values* are swapped — the bindings are correct but
the policy logic is inverted.

### 3. Cross-check with the BGP table

The fastest sanity check is back at `show ip bgp 10.20.0.1`:
- localpref on the path from `172.16.0.1` should be `200`
- localpref on the path from `172.16.0.3` should be `100`

If those numbers are swapped, the route-map bodies are the cause.

## Root cause
Someone edited the route-maps and inverted the `set local-preference` values.
The neighbor statements were never touched, so the names look healthy at a
glance — this is the trap.

## Fix

```
dc-ce-1# configure terminal
dc-ce-1(config)# route-map PRIMARY-PE permit 10
dc-ce-1(config-route-map)#  set local-preference 200
dc-ce-1(config-route-map)# route-map SECONDARY-PE permit 10
dc-ce-1(config-route-map)#  set local-preference 100
dc-ce-1(config-route-map)# end

dc-ce-1# clear ip bgp 172.16.0.1 soft in
dc-ce-1# clear ip bgp 172.16.0.3 soft in
```

The `clear ... soft in` triggers a route-refresh from each PE so the new
local-pref values are applied to the prefixes already in the table — without
this, the table still shows the old values until the next BGP update.

## Verification

```
dc-ce-1# show ip bgp 10.20.0.1
```

Best path should now be via `172.16.0.1` with `localpref 200`. Repeat for
`172.16.0.102/32` and any other branch/DR loopback to confirm.

End-state check:

```
python -m troubleshooting status wan-localpref-reversed   # NO FAULT
```

## Why this teaches you something
"Names lie, values don't." A senior operator never trusts a route-map by
its name — they read the body or check the result in the BGP table. The
correct verification chain is: **policy applied → policy effective → best-path
selected**. You can have any one of those right while another is wrong.
