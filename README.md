# AI Infrastructure Lab

A spec-driven, test-driven, multi-vendor network lab — and the automation
toolchain that builds and validates it. EVE-NG provides the data plane;
this repository provides everything else.

> Source-of-truth lives in YAML and NetBox. Configs, EVE-NG topology, validation
> snapshots, and integration test inventory are all generated from there.
> Nothing on a device is hand-crafted.

---

## What's in the lab

A 17-node multi-vendor topology spread across three sites and a simulated
service-provider transport.

| Site | Devices | Role |
|---|---|---|
| **DC-East** | Arista vEOS spines (×2, EVPN RR cluster) | Underlay + EVPN overlay |
| | Arista vEOS leaves (×2) | Compute leaf VTEPs, anycast gateway |
| | Arista vEOS borders (×2) | North-south fabric handoff |
| | FortiGate VMs (×2) | Active-passive HA pair, port3 heartbeat |
| | Cisco C8000v CE | Dual-homed to both PEs |
| **Branch-01** | Cisco C8000v CE | Dual-homed to both PEs |
| **DR-West** | Arista vEOS leaves (×2) | Collapsed EVPN — leaves peer directly |
| | FortiGate VMs (×2) | Active-passive HA pair |
| | Cisco C8000v CE | Dual-homed to both PEs |
| **WAN transport** | Cisco C8000v PEs (×2) | iBGP between PEs, per-customer eBGP |

**Routing:**

- DC-East: per-leaf unique ASN, eBGP underlay + iBGP EVPN overlay (spines as RRs)
- DR-West: collapsed eBGP underlay + iBGP EVPN, two leaves peer directly
- WAN: per-site customer ASN, eBGP to both PEs, PEs run iBGP between themselves

**Address scheme:** RFC1918 lab ranges only. No public IPs anywhere.

---

## Architecture

The repository implements a closed-loop pipeline. Each step is independently
testable, and every transition is gated:

```
  ┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
  │   NetBox    │───►│  YAML spec   │───►│   configs   │───►│  EVE-NG      │
  │ (intent)    │    │  (per-device │    │  (per       │    │  (data       │
  │             │    │   schema)    │    │   device)   │    │   plane)     │
  └──────┬──────┘    └──────┬───────┘    └──────┬──────┘    └──────┬───────┘
         │                  │                   │                  │
         │ pynetbox         │ Jinja2            │ netmiko          │ live SSH
         │                  │ generator         │ push             │ + agent
         ▼                  ▼                   ▼                  ▼
   populate_netbox     specs/schema.json    Batfish gate       pyATS / Nornir
   netbox_enrich       (jsonschema)         (offline parse,    integration tests
   netbox_bgp          tests/unit/          BGP compatibility, (live state vs
   (idempotent)        (232 unit tests)     reachability)      spec)
```

Each layer has its own validation:

- **NetBox** → unit-tested against pynetbox API contracts
- **Spec** → JSON Schema validation, structural unit tests
- **Configs** → Batfish offline analysis (compatibility, reachability)
- **Live state** → pyATS + Nornir integration tests against the running lab

---

## Capabilities

### Spec-driven config generation

YAML spec is the single source of truth for the lab's intended state. Per-device
configs are rendered via Jinja2 templates per platform (Arista EOS, Cisco IOS-XE,
FortiOS).

```bash
make generate-spec          # NetBox -> YAML
make generate-configs       # YAML + templates -> per-device configs
make validate               # JSON Schema check
make validate-configs       # Per-platform syntax + structural checks
```

### Pre-deploy validation (Batfish)

Every config change is exercised against a Batfish snapshot **before** it
touches a real device. Catches BGP session compatibility issues, reachability
regressions, prefix anomalies, and route-map errors offline.

```bash
make validate-batfish       # Builds snapshot + runs validate.py
```

### Post-deploy live validation

After push, an agent SSH's to every managed device and checks live state against
the spec:

- BGP session establishment (per-platform parsing)
- Interface IP and admin-state drift detection
- ASN, loopback, and VTEP source consistency
- FortiGate HA cluster health

### Test-driven traffic engineering

Integration tests written in pyATS conventions, executed in parallel via Nornir,
parsed via ntc-templates (for cross-vendor coverage). Tests assert observable BGP
attributes (local-pref, AS-path length, prefix counts) on real devices in seconds.

```bash
# Run the full live-lab integration suite — ~7 sec, 23 assertions
pytest tests/integration/test_pyats_baseline.py \
       tests/integration/test_dc_ce1_*.py \
       tests/integration/test_dr_ce1_*.py \
       -m integration -v
```

A red→green TDD cycle demo is included for live demonstration:

```bash
python -m automation.demos.tdd_red_green
```

This intentionally regresses one route-map binding, runs the test (expect RED),
re-applies the binding, runs the test (expect GREEN). Useful for showing the
discipline without faking it.

### Chaos testing

Inject controlled faults (link flap, BGP shutdown, neighbor remove) and assert
the lab recovers with traffic intact. Includes automatic rollback on failure.

```bash
make chaos-test
```

### MCP server

Exposes the most useful read operations as Model Context Protocol tools so an
LLM can query lab state directly:

- `get_topology` — site/device/role/ASN summary from spec
- `check_fabric_health` — BGP underlay state across managed devices
- `check_branch_connectivity` — CE↔PE eBGP state
- `run_batfish_validation` — offline pre-deploy check
- `check_spec_compliance` — drift detection
- `get_device_state` — per-device show output (BGP, interfaces, routes, version)
- `blast_radius` — predict downstream impact of a proposed change
- `run_chaos_test` — controlled fault injection

---

## CI/CD

GitHub Actions runs the full pipeline on every push:

1. **Lint** — `ruff check` + `ruff format --check`
2. **Schema Validation** — every generated spec validates against `specs/schema.json`
3. **Generator Tests** — JSON Schema and config-rendering unit tests
4. **Config Generation Tests** — per-platform structural assertions on rendered configs
5. **All Unit Tests** — 232 tests covering spec, generators, agent skills, chaos, MCP
6. **Batfish Pre-Deploy Validation** — offline snapshot build + validation against the spec

A change must pass all six before merge.

---

## Repository layout

```
specs/
├── schema.json                  JSON Schema for the YAML spec
└── generated/lab_spec.yaml      The intended-state spec (NetBox-derived)

generator/
├── netbox_to_spec.py            NetBox -> YAML
└── render_configs.py            Spec + Jinja2 -> per-device configs

templates/
├── arista/                      Per-role EOS templates (spine, leaf, border-leaf)
├── cisco/                       CE / PE / branch router templates (IOS-XE)
└── fortinet/                    FortiGate HA template (FortiOS)

configs/
├── lab_bootstrap.yaml           Shared OOB / topology layout settings
└── generated/                   Per-device configs (rendered output)

agent/
├── runner.py                    Live state collection + drift comparison
├── skills/
│   ├── fabric_health/           BGP underlay checks
│   ├── branch_connectivity/     CE↔PE checks
│   └── spec_compliance/         Drift detection
└── netbox_reconciler.py         NetBox state sync

automation/
├── inventory.py                 pyATS testbed -> Nornir hosts
├── bgp_state.py                 Vendor-agnostic BGP summary parser
├── runbooks/check_bgp_summary.py Parallel BGP check across managed fabric
└── demos/tdd_red_green.py       Live red→green demo cycle

batfish/
├── snapshot/                    Generated Batfish snapshot (configs + L1 topology)
├── snapshot.py                  Snapshot builder
└── validate.py                  Pre-deploy validation runner

netbox/
├── docker-compose.yml           Dedicated NetBox stack (port 8002)
├── Dockerfile-Plugins           Custom image with netbox-bgp baked in
└── plugins.txt                  Plugin manifest

scripts/
├── generate_testbed.py          Spec -> pyATS testbed.yaml
├── populate_netbox.py           Idempotent NetBox seeding from spec
├── populate_netbox_enterprise.py Tenancy, racks, ASNs, VRFs, VLANs, circuits
├── netbox_enrich.py             Mgmt iface + IP, primary_ip4, ASN, role, serial
├── netbox_bgp_populate.py       BGP sessions in netbox-bgp plugin
├── push_configs.py              Netmiko config push with rollback
├── run_reachability.py          End-to-end traffic verification
├── chaos_test.py                Controlled fault injection
└── credentials.py               Env-driven credential loader (no secrets in code)

mcp_server.py                    MCP server exposing 8+ read tools

tests/
├── unit/                        232 unit tests (schema, generator, agent, chaos)
└── integration/                 Live-lab pyATS tests (marked, opt-in)

docs/
├── topology.md                  Detailed topology reference
├── pipeline.md                  Pipeline-stage walkthrough
├── batfish.md                   Pre-deploy validation guide
├── netbox-data-model.md         NetBox schema usage
├── eve-ng-deployment.md         How configs reach the lab
├── agent-boundary.md            What the agent manages vs. observes
└── testing.md                   Test-pyramid strategy
```

---

## Setup

### Prerequisites

- Python 3.11+
- `uv` (preferred) or `pip` for package management
- Docker (for the NetBox stack and Batfish container)
- An EVE-NG instance reachable from the host running this repo
- SSH access to lab devices on a management network

### Install

```bash
git clone <repo>
cd Eve-NG_Agent
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev,generator,automation]"
```

### Configure

Copy `.env.example` to `.env` and fill in:

- EVE-NG API host + credentials
- NetBox URL + API token
- Device SSH credentials (one set for network devices, one for FortiGates)

`.env` is gitignored. **Never commit credentials or per-deployment IPs.**

### Bring up NetBox (one-time)

```bash
cd netbox
docker compose up -d --build
# wait ~90 sec for first-run migrations
```

Then seed it from the spec:

```bash
python -m scripts.populate_netbox
python -m scripts.populate_netbox_enterprise
python -m scripts.netbox_enrich
python -m scripts.netbox_bgp_populate
```

### Common workflows

| Workflow | Command |
|---|---|
| Generate spec from NetBox | `make generate-spec` |
| Render configs from spec | `make generate-configs` |
| Validate spec | `make validate` |
| Pre-deploy Batfish check | `make validate-batfish` |
| Push to lab | `make push-configs` |
| Full safe deploy | `make deploy-safe` |
| Run all unit tests | `make test` |
| Run live integration tests | `pytest tests/integration/ -m integration -v` |
| Lint + auto-fix | `make lint-fix` |
| Chaos test | `make chaos-test` |

---

## Standards

The repo enforces three project-wide standards. Each is referenced by ID in
commit messages and reviews:

- **PIS** — Project Initiation Standard (30 rules, spec precision through
  cost/token economics)
- **IDS** — Infrastructure Design Standard (36 rules, network design choices)
- **SEC** — Secure Build Standard (33 rules, security-by-default)

Anti-hallucination verification is baked in:

- Every output is cross-referenced against a second independent source
- Both sides of bidirectional relationships are checked (if A peers with B,
  the test verifies the session is up on both ends)
- Structured/parsed data is preferred over regex on raw output
- Control-plane state ("config says X") is distinguished from data-plane
  truth ("traffic actually flows") — both are tested where relevant

---

## What this lab is good for

- Practicing real BGP traffic engineering on real (virtualized) gear
- Building and exercising automation against multi-vendor inventory
- Demonstrating spec-driven, test-driven network engineering as a discipline
- Stress-testing CI/CD patterns on infra (the gate runs Batfish + 232 tests
  per push)
- A safe place to make destructive changes — every step is rollback-aware

---

## Acknowledgements

Built on top of:

- [EVE-NG](https://www.eve-ng.net/) — virtualization platform
- [NetBox](https://github.com/netbox-community/netbox) — source of truth
- [netbox-bgp plugin](https://github.com/netbox-community/netbox-bgp) — BGP topology in NetBox
- [Batfish](https://www.batfish.org/) — offline network analysis
- [pyATS](https://developer.cisco.com/pyats/) / Genie — test framework
- [Nornir](https://nornir.readthedocs.io/) — parallel automation runner
- [ntc-templates](https://github.com/networktocode/ntc-templates) — vendor parsing
- [netmiko](https://github.com/ktbyers/netmiko) — SSH transport
- [Jinja2](https://jinja.palletsprojects.com/) — config templating
