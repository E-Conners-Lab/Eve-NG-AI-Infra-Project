# Pipeline — NetBox to Validated Deployment

## Full Flow

```
NetBox (source of truth)
  │
  ▼
generator/netbox_to_spec.py ──► specs/generated/lab_spec.yaml
  │
  ▼
generator/render_configs.py ──► configs/generated/*.cfg (17 devices)
  │
  ▼
batfish/validate.py ──► Batfish Digital Twin (pre-deploy gate)
  │                     • BGP session compatibility
  │                     • Reachability matrix
  │                     • Failure impact analysis
  │  PASS?
  ▼
scripts/push_configs.py ──► EVE-NG devices (SSH/Netmiko)
  │
  ▼
agent/runner.py ──► Live validation (3 skills)
  │                 • fabric_health (BGP sessions)
  │                 • branch_connectivity (CE dual-homing)
  │                 • spec_compliance (config drift)
  │
  ├──► Telegram notifications
  └──► NetBox journal entries (if ENABLE_NETBOX_RECONCILIATION=true)
```

## Pipeline Stages

### 1. Spec Generation (`make generate-spec`)
Reads all data from NetBox via pynetbox: sites, devices, interfaces, IPs, cables, config contexts. Produces a single YAML spec file that is the contract between NetBox and everything downstream.

### 2. Config Rendering (`make generate-configs`)
Reads the YAML spec and renders per-device configs using Jinja2 templates. One template per platform+role combination (7 templates total). All values come from the spec — zero hardcoded IPs, ASNs, or credentials.

### 3. Batfish Validation (`make validate-batfish`)
Builds a Batfish snapshot from the generated configs and spec topology data. Runs pre-deployment checks:
- **BGP session compatibility** — all underlay sessions must be UNIQUE_MATCH
- **Reachability matrix** — all 6 host-to-host paths must be reachable
- **Failure impact** — simulate spine/border failures and verify ECMP absorbs them

This gate blocks deployment if critical issues are found. Emergency bypass: `--skip-batfish` in the deploy workflow.

### 4. Config Push (`make push-configs`)
Pushes configs to EVE-NG devices via Netmiko SSH. Platform-specific handling:
- **Arista**: `send_config_set` + `enable()` + `write memory`
- **Cisco**: `send_config_set` + `enable()` + `write memory`
- **FortiGate**: `write_channel` (fire-and-forget), HA block skipped

### 5. Agent Validation (`make agent`)
Connects to all managed devices and runs 3 skills:
- **fabric_health**: Parses `show ip bgp summary` on Arista devices
- **branch_connectivity**: Verifies dual-homed BGP on CE routers
- **spec_compliance**: Compares live interface IPs against spec

### 6. Reporting
- **Telegram**: Real-time notifications per device check
- **NetBox**: Journal entries for detected drift (gated, deduplicated)
- **GAIT logs**: JSON Lines audit trail for every action

## CI/CD Pipelines

### CI (on push/PR to main)
```
lint → schema-validation → generator → config-gen → unit-tests + batfish-validation
```

### Deploy (manual workflow_dispatch)
```
CI → batfish-gate → push-configs → reachability → agent-validation
```
