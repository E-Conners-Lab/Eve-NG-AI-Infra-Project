# Runbook — `l1-iface-description-swap`

**Device:** `dc-border-1` &nbsp;|&nbsp; **Platform:** Arista vEOS &nbsp;|&nbsp; **Difficulty:** beginner

## Symptom
An operator was asked to drain traffic on the dc-spine-2 uplink. They ran
`show interfaces description`, saw `Et1 ... to dc-spine-2`, shut Et1, and
took down the wrong session — the dc-spine-1 link is what actually went
down. Wiring is unchanged; only the descriptions are wrong.

## Hint ladder
1. Don't trust labels — verify with the protocol state.
2. The L3 next-hop on each link tells you which spine you're really talking to.
3. Look at `show lldp neighbors` if available — the peer-side hostname is authoritative.

## Diagnosis

### 1. Read the descriptions

```
dc-border-1# show interfaces description
Interface  Status  Protocol  Description
Et1        up      up        to dc-spine-2
Et2        up      up        to dc-spine-1
```

So far so good — except this is what the *config* says, not what the link
actually connects to.

### 2. Verify against the L3 next-hop

The spec assigns `10.1.1.4/31` to dc-spine-1's side of the Et1 link, and
`10.1.1.12/31` to dc-spine-2's side of Et2. Check what's on the wire:

```
dc-border-1# show ip interface Ethernet1 | include Internet
  Internet address is 10.1.1.5/31
dc-border-1# show ip interface Ethernet2 | include Internet
  Internet address is 10.1.1.13/31
```

`10.1.1.5/31` pairs with `10.1.1.4` (spine-1 underlay). So **Et1 actually
goes to spine-1**, despite the description saying otherwise.

### 3. Cross-check with LLDP

```
dc-border-1# show lldp neighbors
Port    Neighbor Device ID    Neighbor Port ID
Et1     dc-spine-1            Ethernet3
Et2     dc-spine-2            Ethernet3
```

LLDP is authoritative — the remote device tells you its hostname. Et1 →
dc-spine-1, Et2 → dc-spine-2. The descriptions lie.

### 4. Check the BGP peer IPs

```
dc-border-1# show ip bgp summary
```

Neighbor `10.1.1.4` (dc-spine-1) is reached via the Et1 next-hop. Anyone
chasing the description would shut the wrong session.

## Root cause
Someone edited descriptions during a config push and swapped the labels.
The wiring, IPs, and BGP sessions are all correct — only the human-readable
metadata is wrong.

## Fix

```
dc-border-1# configure terminal
dc-border-1(config)# interface Ethernet1
dc-border-1(config-if-Et1)#  description to dc-spine-1
dc-border-1(config-if-Et1)# interface Ethernet2
dc-border-1(config-if-Et2)#  description to dc-spine-2
dc-border-1(config-if-Et2)# end
```

## Verification

```
dc-border-1# show interfaces description     # Et1 -> dc-spine-1, Et2 -> dc-spine-2
dc-border-1# show lldp neighbors              # matches descriptions
```

```
python -m troubleshooting status l1-iface-description-swap   # NO FAULT
```

## Why this teaches you something
**Descriptions are documentation, not state.** Anything humans type can
disagree with what's actually plumbed. The state-of-truth chain for "what
does this interface really connect to?" is:

1. **LLDP/CDP** — what the peer thinks its own name is.
2. **L3 next-hop / BGP peer IP** — verifiable against the IP plan.
3. **Description** — only useful if (1) and (2) confirm it.

Senior operators read descriptions for context but never trust them.
