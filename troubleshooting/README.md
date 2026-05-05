# Troubleshooting Scenarios

Curated, named faults you can inject into the live EVE-NG lab and then
diagnose **from the device consoles**, the way you'd troubleshoot a real
network. The framework breaks something for you; you go solve it.

## Workflow

```
# What scenarios are there?
python -m troubleshooting list

# Look at a scenario's symptoms (no spoilers)
python -m troubleshooting show wan-localpref-reversed

# Break it
python -m troubleshooting inject wan-localpref-reversed

# --- now SSH/console into the affected device(s) and troubleshoot ---
ssh admin@<dc-ce-1-mgmt-ip>
dc-ce-1# show ip bgp summary
... etc.

# Check whether the fault is still present (no spoilers — only yes/no)
python -m troubleshooting status wan-localpref-reversed

# Give up? Read the runbook for the answer
python -m troubleshooting runbook wan-localpref-reversed

# Apply the targeted fix programmatically (the answer key)
python -m troubleshooting fix wan-localpref-reversed

# Or push the full clean spec config — nuclear reset
python -m troubleshooting restore wan-localpref-reversed
```

`status` deliberately does not reveal *what* was injected — only whether
a fault is currently present. This keeps the exercise honest.

## Implemented scenarios

### Beginner — physical / labels
| Name | Device | Tests what |
|---|---|---|
| `l1-iface-admin-down` | dc-border-1 | mapping a broken BGP neighbor back to its physical interface |
| `l1-iface-description-swap` | dc-border-1 | not trusting `show interfaces description` over LLDP / next-hop reality |

### Intermediate — WAN BGP policy
| Name | Device | Tests what |
|---|---|---|
| `wan-localpref-reversed` | dc-ce-1 | reading route-map *bodies*, not their names |
| `wan-bgp-timer-mismatch` | dc-ce-1 | recognising session flaps as a timer/keepalive issue |
| `wan-prefix-filter-typo` | dc-ce-1 | enumerating multiple inbound filters per neighbor |
| `wan-md5-auth-mismatch` | dc-ce-1 | reading "Active/Idle never" as a TCP/auth problem, not an FSM stall |
| `wan-aspath-prepend-wrong-direction` | dr-ce-1 | verifying outbound TE via `advertised-routes`, not local table |

### Advanced — EVPN, fabric, multi-fault
| Name | Device | Tests what |
|---|---|---|
| `evpn-vni-mapping-missing` | dc-leaf-2 | control plane vs. data plane in EVPN/VXLAN |
| `evpn-rr-peer-missing` | dc-spine-1 | per-AF activation — `show ip bgp` is not `show bgp evpn` |
| `evpn-wrong-rt-import` | dc-leaf-2 | import-RT vs export-RT independence; BGP RIB vs L2 RIB |
| `evpn-anycast-gw-mac-mismatch` | dc-leaf-1 | state inconsistency between boxes that should be identical |
| `underlay-mtu-mismatch` | dc-leaf-1 | "control plane green, data plane red" → MTU |
| `multi-fault-localpref-and-iface` | dc-ce-1 + dc-border-1 | not stopping at the first thing you find |

Each has a runbook in `troubleshooting/runbooks/<name>.md` with diagnostic
steps, expected `show`-command output, the root cause, and the fix.

## Planned scenarios (not yet implemented)

| Name | Device | Difficulty | Notes |
|---|---|---|---|
| `ha-fw-heartbeat-down` | dc-fw-1 | advanced | shut HA heartbeat interface → split-brain (FortiGate-specific) |
| `ha-fw-policy-missing` | dc-fw-1 | intermediate | new subnet not in policy — pings work, app traffic doesn't |

## Authoring a new scenario

```
1. Write tests under tests/unit/troubleshooting/test_<name>.py
   - inject sends the expected commands
   - detect returns True for fault output, False for clean output
   - fix sends the inverse commands
   - the scenario is registered

2. Run pytest — RED.

3. Implement troubleshooting/scenarios/<name>.py:
   - inject(conn), detect(conn) -> (bool, str), fix(conn)
   - SCENARIO = Scenario(...)
   - REGISTRY.register(SCENARIO)

4. Add `from . import <name>` to scenarios/__init__.py.

5. Run pytest — GREEN.

6. Write troubleshooting/runbooks/<name>.md with the answer key.
```

The framework reuses the existing project plumbing — `scripts.bootstrap_config`
for management IPs, `scripts.credentials` for SSH login, `scripts.push_configs`
for clean-config restore. There's no second inventory to maintain.

## Safety

- `inject` never affects sp-pe-1 / sp-pe-2 (they're outside the agent boundary).
- `restore` always pushes the spec-rendered config from `configs/generated/`,
  so you can always get back to a known-good state regardless of how badly
  a session went.
- Scenarios are **idempotent** in the practical sense: re-injecting on top
  of an already-injected fault is a no-op or a re-application of the same
  state; re-fixing on a clean device is a no-op.
- The framework does not reach outside the lab. All actions are SSH to
  managed devices on the management network.
