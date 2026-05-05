# Runbook — `wan-bgp-timer-mismatch`

**Device:** `dc-ce-1` &nbsp;|&nbsp; **Platform:** Cisco IOS-XE &nbsp;|&nbsp; **Difficulty:** intermediate

## Symptom
BGP session from dc-ce-1 to sp-pe-1 (`172.16.0.1`) keeps flapping every
minute or two. sp-pe-2 is fine. Routes from sp-pe-1 disappear and reappear,
which churns the local-pref selection and creates intermittent path changes.

## Hint ladder
1. Look at the uptime in `show ip bgp summary` — anything under a few minutes is suspicious.
2. The session is flapping *cleanly* (Established → Idle → Established). What protocol mechanism causes that?
3. Compare *configured* vs *negotiated* timers on the two PE neighbors.

## Diagnosis

### 1. Spot the flapping session

```
dc-ce-1# show ip bgp summary
```

`Up/Down` for `172.16.0.1` will show seconds or low minutes; `172.16.0.3`
will show hours.

### 2. Check the logs

```
dc-ce-1# show logging | include BGP
```

Look for repeating `%BGP-5-ADJCHANGE: neighbor 172.16.0.1 ... Down ...`
followed by `Up` events. The reason will say `holdtime expired` or
`BGP Notification sent`.

### 3. Compare timers on the two neighbors

```
dc-ce-1# show ip bgp neighbors 172.16.0.1 | include hold time
```

You'll see something like:

```
Last read 00:00:01, last write 00:00:00, hold time is 15, keepalive interval is 5 seconds
Configured hold time is 15, keepalive interval is 5 seconds
```

Now compare:

```
dc-ce-1# show ip bgp neighbors 172.16.0.3 | include hold time
```

The healthy neighbor should show `hold time is 180, keepalive interval is 60`.

A 15-second hold time on a lab link is far too aggressive — single-digit
millisecond jitter or a brief CPU spike causes a missed keepalive, and the
session resets.

### 4. Confirm in the running config

```
dc-ce-1# show running-config | section router bgp
```

Look for:

```
neighbor 172.16.0.1 timers 5 15
```

That line is the smoking gun.

## Root cause
Someone tuned BGP timers down ("for faster convergence") on the primary PE
neighbor without coordinating with sp-pe-1. On lossy or slightly
oversubscribed links the keepalives miss and the hold timer fires.

## Fix
Restore default timers:

```
dc-ce-1# configure terminal
dc-ce-1(config)# router bgp 65100
dc-ce-1(config-router)#  no neighbor 172.16.0.1 timers
dc-ce-1(config-router)# end
```

The session will reset once and then come up cleanly with default 60/180
timers.

## Verification

```
dc-ce-1# show ip bgp summary
```

Wait two minutes; `Up/Down` for 172.16.0.1 should keep climbing without
resetting.

```
python -m troubleshooting status wan-bgp-timer-mismatch   # NO FAULT
```

## Why this teaches you something
Aggressive BGP timers are a frequent over-correction in environments
chasing fast failover. The right answer is almost always **BFD** with
default BGP timers — let BFD detect liveness in milliseconds, and let BGP
keepalives stay relaxed. A 5/15 BGP timer is a footgun that creates the
exact instability it pretends to detect.
