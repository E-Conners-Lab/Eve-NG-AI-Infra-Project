# Runbook — `wan-aspath-prepend-wrong-direction`

**Device:** `dr-ce-1` &nbsp;|&nbsp; **Platform:** Cisco IOS-XE &nbsp;|&nbsp; **Difficulty:** intermediate

## Symptom
Asymmetric routing for traffic into DR. Outbound traffic from dr-ce-1
exits via sp-pe-1 (primary) as expected — local-pref policy is fine.
But **return** traffic from elsewhere on the SP cloud comes back via
**sp-pe-2** instead of sp-pe-1. A stateful firewall in either path will
start logging dropped half-flows.

## Hint ladder
1. Outbound and inbound are decided by *different* policies. Local-pref
   only controls what *we* prefer, not what the SP prefers.
2. The SP picks the shortest AS-path. If we prepend ours, we make our path
   look longer.
3. The route-map that does the prepend has to be bound out to the *right*
   neighbor. Check which one.

## Diagnosis

### 1. Confirm asymmetry

On dr-ce-1:

```
dr-ce-1# show ip route 0.0.0.0      (or any branch / DC prefix)
```

Outbound next-hop should be 172.16.0.10 (sp-pe-1). Then look at how the
remote side is reaching *us*:

```
dr-ce-1# show ip bgp 172.16.0.103/32 | begin advertised
```

(or use traceroute from dc-host-1 → dr-host-1 and observe the path on the
return). Return traffic going via sp-pe-2 confirms inbound asymmetry.

### 2. Look at what the SP sees from each of our paths

We can't log into sp-pe-*, but we can read what we *advertise* outbound:

```
dr-ce-1# show ip bgp neighbors 172.16.0.10 advertised-routes
dr-ce-1# show ip bgp neighbors 172.16.0.12 advertised-routes
```

For our local prefix (e.g. `10.30.0.1/32`):

- The advertisement to **172.16.0.10** has AS-path `65130 65130 65130` (prepended).
- The advertisement to **172.16.0.12** has AS-path `65130` (not prepended).

That's backwards. We *want* the prepend on the secondary PE so the SP
prefers the primary.

### 3. Confirm which neighbor LONG-PATH-OUT is bound to

```
dr-ce-1# show running-config | section router bgp
```

Look for:

```
neighbor 172.16.0.10 route-map LONG-PATH-OUT out      <-- WRONG: bound to primary
```

Healthy state has it bound to `172.16.0.12` instead.

### 4. Read the route-map body to confirm what it does

```
dr-ce-1# show route-map LONG-PATH-OUT
```

```
route-map LONG-PATH-OUT, permit, sequence 10
  Set clauses:
    as-path prepend 65130 65130
```

So the route-map prepends our ASN twice. Bound to the wrong neighbor, it
makes us look bad on the path we're trying to *use*.

## Root cause
Someone moved the route-map binding to the wrong neighbor — either during
a copy-paste change window, or because they confused "the route-map that
makes the path bad" with "the route-map that goes on the good neighbor."
Naming it `LONG-PATH-OUT` rather than `SECONDARY-PREPEND-OUT` doesn't
help — the name describes *what it does*, not *where it goes*.

## Fix

```
dr-ce-1# configure terminal
dr-ce-1(config)# router bgp 65130
dr-ce-1(config-router)#  address-family ipv4
dr-ce-1(config-router-af)#   no neighbor 172.16.0.10 route-map LONG-PATH-OUT out
dr-ce-1(config-router-af)#   neighbor 172.16.0.12 route-map LONG-PATH-OUT out
dr-ce-1(config-router-af)# end

dr-ce-1# clear ip bgp 172.16.0.10 soft out
dr-ce-1# clear ip bgp 172.16.0.12 soft out
```

## Verification

```
dr-ce-1# show ip bgp neighbors 172.16.0.12 advertised-routes
```

The AS-path for our local prefixes should now be `65130 65130 65130`.

```
dr-ce-1# show ip bgp neighbors 172.16.0.10 advertised-routes
```

AS-path should be just `65130` (no prepend).

```
python -m troubleshooting status wan-aspath-prepend-wrong-direction   # NO FAULT
```

## Why this teaches you something
**Outbound TE is harder to verify than inbound TE.** With local-pref, you
can prove the policy worked by reading your own BGP table — `show ip bgp
<prefix>` tells you the answer. With AS-path prepending, the policy only
takes effect on the *peer's* RIB, which you can't see directly. The
closest proxy is `show ip bgp neighbors X advertised-routes` — read what
*you sent*, not what *you have*. Asymmetric routing always means
"outbound and inbound disagree" — verify them separately.
