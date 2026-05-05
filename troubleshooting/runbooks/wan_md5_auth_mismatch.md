# Runbook — `wan-md5-auth-mismatch`

**Device:** `dc-ce-1` &nbsp;|&nbsp; **Platform:** Cisco IOS-XE &nbsp;|&nbsp; **Difficulty:** intermediate

## Symptom
BGP session from dc-ce-1 to sp-pe-1 (`172.16.0.1`) won't come up. The
neighbor is stuck in `Active` (or oscillating Active → Idle), `MsgRcvd`
stays at 0, and `Up/Down` shows `never`. sp-pe-2 is Established as normal.
All traffic is going via the secondary PE.

## Hint ladder
1. The session never reaches Established. What FSM stage are we stuck at?
2. The TCP layer is doing something. Check `show tcp brief` for TCP-179.
3. BGP sessions can fail to establish for L1, L3, or L4 reasons. We have routing — what about L4?

## Diagnosis

### 1. Confirm the session state

```
dc-ce-1# show ip bgp summary
Neighbor    State/PfxRcd
172.16.0.1  Active
172.16.0.3  4
```

Stuck Active is the fingerprint of "TCP can't complete." Compare with the
healthy neighbor.

### 2. Verify L3 reachability (rule out routing)

```
dc-ce-1# ping 172.16.0.1
```

Should be 100%. If pings work, the problem is at L4 or above.

### 3. Watch the TCP state

```
dc-ce-1# show tcp brief | include 172.16.0.1
```

You'll often see TCBs being created and torn down. With MD5 mismatch the
SYN gets a RST (or the option is dropped), and the FSM never reaches
ESTAB. `show tcp brief numeric` over a few seconds shows churn.

### 4. Look at debug or syslog

```
dc-ce-1# show logging | include BGP
```

Look for:

```
%TCP-6-BADAUTH: Invalid MD5 digest from 172.16.0.1(...) to 172.16.0.101(179)
```

That's the smoking gun. TCP-6-BADAUTH = MD5 mismatch.

### 5. Inspect the neighbor config

```
dc-ce-1# show running-config | section router bgp
```

Look for:

```
neighbor 172.16.0.1 password TS-WRONG-PW
```

If sp-pe-1's config (or your spec/peer change ticket) doesn't show a
matching password, that's the cause.

## Root cause
A BGP password was set on dc-ce-1 (perhaps as part of a security review
or password-rotation change) but the matching configuration was never
applied to sp-pe-1. The TCP MD5 option mismatches and the three-way
handshake fails.

## Fix
Either remove the password locally, or coordinate the rotation with the
SP. For this exercise we remove it on dc-ce-1:

```
dc-ce-1# configure terminal
dc-ce-1(config)# router bgp 65100
dc-ce-1(config-router)#  no neighbor 172.16.0.1 password
dc-ce-1(config-router)# end
```

The session converges within a few seconds (next TCP attempt succeeds).

## Verification

```
dc-ce-1# show ip bgp summary | include 172.16.0.1
```

State changes from `Active` → `OpenSent` → `OpenConfirm` → `Established`.
PfxRcd should match sp-pe-2 once stable.

```
python -m troubleshooting status wan-md5-auth-mismatch   # NO FAULT
```

## Why this teaches you something
**`Active` is not a transient state.** Many junior operators see "Active"
and assume the neighbor is "trying to come up" and will eventually make
it. In practice, persistent `Active` always means the TCP-179 handshake
is failing — which narrows the cause to a small list:

- **MD5 password mismatch** (this scenario)
- **ACL or firewall** dropping TCP-179 in one direction
- **TTL-security / GTSM** mismatch
- **Wrong remote-as** on one side (TCP succeeds, BGP OPEN fails — but the
  symptom can look similar at-a-glance)

Reading `show logging` or `show tcp brief` is faster than guessing — let
the box tell you which one of those is wrong.
