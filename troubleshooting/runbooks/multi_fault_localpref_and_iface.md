# Runbook — `multi-fault-localpref-and-iface`

**Devices:** `dc-ce-1` + `dc-border-1` &nbsp;|&nbsp; **Platforms:** Cisco IOS-XE + Arista vEOS &nbsp;|&nbsp; **Difficulty:** advanced

## User-reported symptom
"Our outbound traffic from DC is going to the wrong PE again — sp-pe-2 is
the primary path. Same as last week. Can someone fix it?"

## What you'll find while investigating
Two things are broken at once:

1. **Real cause:** dc-ce-1's local-pref policy is inverted (this is what
   the user is reporting).
2. **Red herring:** dc-border-1 has Ethernet1 admin-shut. It's loud — it
   shows up the moment you run `show ip bgp summary` anywhere on the DC
   spines and a chat bot probably already paged about it.

If you fix the red herring and stop, the user-reported symptom is *still*
there. The temptation to declare victory after the obvious fix is exactly
what this scenario tests.

## Hint ladder
1. The user said *outbound traffic is going to the wrong PE*. Trace what
   they actually said back to the device that decides outbound — it's
   not the border, it's the CE.
2. A loud incident next to a quiet one is rarely a coincidence in
   conversation, but they often *are* unrelated in the network. Verify
   each fault has a clear causal link to the symptom before assigning
   blame.
3. After fixing one thing, **re-validate the original symptom**, not just
   the thing you fixed.

## Diagnosis

### 1. Start at the symptom

```
dc-ce-1# show ip bgp 10.20.0.1
```

Best path is via `172.16.0.3` (sp-pe-2) with localpref 200. This is
the user's complaint. See `wan-localpref-reversed.md` for the deep dive.

### 2. Notice the incidental fault but don't get distracted

While poking around the fabric you'll see:

```
dc-spine-1# show ip bgp summary
Neighbor      Up/Down    State/PfxRcd
10.1.1.5      00:01:32   Idle
```

dc-border-1's session over Et1 is down. See `l1-iface-admin-down.md` for
the unwind.

**Key check:** does fixing the border interface change the user-reported
symptom? Reach for paper before the keyboard. The border-1 link going
down doesn't affect *which PE dc-ce-1 prefers* — it's the wrong device,
the wrong layer, and the wrong direction. So no, fixing it won't help
the symptom. Fix it anyway (it's broken), but do it knowing it's not
the answer.

### 3. Apply both fixes, in either order

```
# Fix 1 — the red herring (because it's broken)
dc-border-1(config)# interface Ethernet1
dc-border-1(config-if-Et1)#  no shutdown

# Fix 2 — the actual cause
dc-ce-1(config)# route-map PRIMARY-PE permit 10
dc-ce-1(config-route-map)#  set local-preference 200
dc-ce-1(config-route-map)# route-map SECONDARY-PE permit 10
dc-ce-1(config-route-map)#  set local-preference 100
dc-ce-1(config-route-map)# end
dc-ce-1# clear ip bgp 172.16.0.1 soft in
dc-ce-1# clear ip bgp 172.16.0.3 soft in
```

### 4. Re-validate against the user's actual symptom

```
dc-ce-1# show ip bgp 10.20.0.1
```

Best path now via `172.16.0.1` (sp-pe-1), localpref 200. **This** is the
proof you can close the ticket on.

## Verification

```
python -m troubleshooting status multi-fault-localpref-and-iface   # NO FAULT
```

Both layers must be clean. The framework's detect returns FAULT PRESENT
if *either* sub-fault is still active — so partial fixes are caught.

## Why this teaches you something
**Stop using "the first thing I found" as a stopping criterion.** Real
incidents are sometimes correlated, sometimes coincident, sometimes
totally independent. The discipline is:

1. **Anchor on the user's symptom.** Write it down before you start
   poking. ("Outbound traffic from DC exits via the wrong PE.")
2. **Every fix must have a stated causal link to that symptom.**
   "Bringing dc-border-1's uplink back up restores ECMP for north-south
   traffic" — useful, but doesn't explain "wrong PE selection."
3. **After every fix, re-test the symptom.** Not just the thing you
   touched.

The other lesson: **multiple faults can share a window without sharing a
cause.** A change window that touched 30 devices can leave behind a few
unrelated mistakes. Treating them as one cause leads to the
declare-victory-too-early failure mode.
