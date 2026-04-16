# AI Infrastructure Lab — EVE-NG Agent

## Project Overview

Multi-site, multi-vendor, HA network lab running in EVE-NG on Proxmox. Managed by an AI agent built on NetClaw. The workflow is spec-driven: NetBox → YAML spec → per-device configs → EVE-NG → continuous validation.

## Architecture

- **Source of Truth:** NetBox (REST API, pynetbox)
- **Spec Format:** Declarative YAML, generated from NetBox, never hand-edited
- **Config Generation:** Python + Jinja2 templates
- **Lab Platform:** EVE-NG on Proxmox (Dell PowerEdge R640)
- **Agent Foundation:** NetClaw (OpenClaw-based, Claude Code, SKILL.md pattern)
- **Device Connectivity:** pyATS MCP (Cisco), gNMI MCP (Arista)
- **CI/CD:** GitHub Actions
- **Testing:** pytest, TDD

## Topology — 21 Nodes, 3 Sites

### DC-East (EVPN-VXLAN Fabric)
- dc-spine-1, dc-spine-2 — Arista vEOS 4.33.1.1F, iBGP EVPN RR, AS 65000
- dc-leaf-1 — Arista vEOS, compute/VTEP, AS 65001, VNI 10100
- dc-leaf-2 — Arista vEOS, compute/VTEP, AS 65002, VNI 10200
- dc-border-1 — Arista vEOS, fabric handoff, AS 65003
- dc-border-2 — Arista vEOS, fabric handoff, AS 65004
- dc-host-1 — Alpine Linux, 10.10.1.10 (VNI 10100)
- dc-host-2 — Alpine Linux, 10.10.2.10 (VNI 10200)
- Underlay: eBGP (per-leaf ASN), Overlay: iBGP EVPN (spines as RR)
- VXLAN: VNI-to-VLAN on compute leaves, distributed anycast gateway

### DC Security — FortiGate HA
- dc-fw-1 (active), dc-fw-2 (standby)
- dc-border-1 → dc-fw-1, dc-border-2 → dc-fw-2, both FWs → dc-ce-1

### WAN — Simulated SP Transport
- dc-ce-1 — Cisco C8000v, AS 65100 (dual-homed to sp-pe-1/sp-pe-2)
- sp-pe-1, sp-pe-2 — Cisco C8000v, AS 64500
- PE-to-PE is L2 transport; everything else is L3

### Branch-01
- br-ce-1 — Cisco C8000v, AS 65100 (dual-homed to sp-pe-1/sp-pe-2)
- br-host-1 — Alpine Linux, 10.20.1.10

### DR-West (Collapsed EVPN)
- dr-leaf-1 (AS 65201), dr-leaf-2 (AS 65202) — direct eBGP + iBGP EVPN
- dr-fw-1 (active), dr-fw-2 (standby)
- dr-ce-1 — Cisco C8000v, AS 65100 (dual-homed)
- dr-host-1 — Alpine Linux, 10.30.1.10

**Totals: 8 Arista, 5 Cisco, 4 FortiGate, 4 Linux — 21 nodes**

## Addressing

| Segment | Range |
|---------|-------|
| DC underlay loopbacks | 10.1.0.0/24 |
| DC underlay P2P | 10.1.1.0/24 (/31 pairs) |
| DC VTEP loopbacks | 10.1.2.0/24 |
| DC overlay VNI 10100 | 10.10.1.0/24 |
| DC overlay VNI 10200 | 10.10.2.0/24 |
| Border-to-FW transit | 10.99.0.0/24 |
| FW-to-CE transit | 10.99.1.0/24 |
| SP transport | 172.16.0.0/24 (/31 pairs) |
| Branch | 10.20.0.0/16 |
| DR overlay | 10.30.0.0/16 |
| DR underlay | 10.31.0.0/24 |

## Agent Boundary

**Managed (config + monitor):** dc-spine-1, dc-spine-2, dc-leaf-1, dc-leaf-2, dc-border-1, dc-border-2, dc-fw-1, dc-fw-2, dc-ce-1, dc-host-1, dc-host-2, br-ce-1, br-host-1, dr-leaf-1, dr-leaf-2, dr-fw-1, dr-fw-2, dr-ce-1, dr-host-1

**Observed (read-only interfaces):** dc-border-1:Ethernet3, dc-border-2:Ethernet3 (toward firewalls), dr-leaf-1:Ethernet2, dr-leaf-2:Ethernet2 (toward firewalls)

**Excluded:** sp-pe-1, sp-pe-2 (service provider — not customer equipment)

## Pipeline

1. Engineer changes NetBox → 2. Generator renders YAML spec → 3. CI validates (schema, config gen, lint) → 4. Configs produced as artifacts → 5. Deploy to EVE-NG → 6. Agent validates live state vs spec → 7. Drift reported via Slack

## Development Standards

- **TDD**: Tests derived from spec, written before implementation
- **Linting**: `ruff check` and `ruff format` after every edit
- **Testing**: `pytest` after every meaningful change
- **Commits**: Small, focused, conventional commit messages

## Commands

```bash
make test              # Run all tests
make lint              # Run ruff check + format
make validate          # Validate specs against schema
make generate-spec     # Generate YAML spec from NetBox
make generate-configs  # Render per-device configs from spec
```

## Key Paths

- `specs/schema.json` — JSON Schema for YAML spec validation
- `specs/generated/` — Generated YAML specs (git-tracked)
- `templates/{arista,cisco,fortinet}/` — Jinja2 config templates
- `configs/generated/` — Rendered per-device configs
- `agent/skills/` — NetClaw agent skill definitions
- `tests/unit/` — Unit tests (schema, config gen)
- `tests/integration/` — Live validation tests

## Standards

- **PIS** (Project Initiation Standard) — 30 rules
- **IDS** (Infrastructure Design Standard) — 36 rules
- **SEC** (Secure Build Standard) — 33 rules
