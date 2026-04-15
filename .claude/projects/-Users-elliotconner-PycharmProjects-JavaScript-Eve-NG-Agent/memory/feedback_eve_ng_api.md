---
name: EVE-NG API Node Creation Gotchas
description: Critical parameters for creating nodes via EVE-NG API that differ from UI defaults — config must be "0", C8000v needs 4 CPUs, Alpine uses e0/e1 labels
type: feedback
---

## EVE-NG API node creation requires exact parameter matching with UI defaults

When creating nodes via the EVE-NG REST API, several parameters must match what the UI would set, or nodes will crash on startup.

**Why:** API-created nodes with wrong defaults cause silent boot failures — QEMU starts, creates TAP interfaces, then dies within 1 second with no useful error in logs.

**How to apply:**

### config parameter (CRITICAL)
- API must set `config: "0"` (not `"none"`, not `"Unconfigured"`)
- `"none"` maps to `"Unconfigured"` in EVE-NG XML, which causes instant boot failure
- `"0"` means "no startup config, just boot" — this is what the UI sets

### C8000v (Cisco Catalyst 8000V) requirements
- **cpu: 4** minimum (not 1) — IOS-XE kernel panics with fewer CPUs
- **ram: 8192** (8GB) — template default, don't reduce
- **ethernet: 4** default — can go to 5 for management, but 8 causes 500 errors
- Template name: `c8000v`, image folder must match exactly

### Arista vEOS
- **ram: 2048**, **cpu: 1** works fine
- **ethernet: 8** — interfaces labeled Mgmt1, Eth1-Eth7 in EVE-NG

### FortiGate
- **ram: 2048**, **cpu: 1** works fine
- **ethernet: 6** — interfaces labeled port1-port6 in EVE-NG

### Alpine Linux
- **ram: 512**, **cpu: 1**
- **ethernet: 2** — interfaces labeled `e0`, `e1` (NOT eth0, eth1)
- Must install from ISO via QEMU directly, not cloud-init images

### EVE-NG Pro path handling
- Labs are stored at root `/`, not under `/admin/`
- evengsdk's `normalize_path` prepends `/admin/` unless path starts with `/`
- Always pass lab paths with leading `/` when using evengsdk

### Lab locking
- Rapid successive API calls cause 400/412 "lab locked" errors
- Use evengsdk's `connect_node_to_node()` for wiring — it handles locking correctly
- Don't mix custom HTTP client and evengsdk in the same session
