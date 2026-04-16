# Agent Boundary

The agent boundary defines which devices the AI agent can configure, monitor, and report on. It is defined in the YAML spec under `agent.boundary` and enforced in every skill.

## Boundary Classification

| Device | Site | Platform | Role | Boundary | Rationale |
|--------|------|----------|------|----------|-----------|
| dc-spine-1 | DC-East | Arista vEOS | spine | **Managed** | Customer fabric infrastructure |
| dc-spine-2 | DC-East | Arista vEOS | spine | **Managed** | Customer fabric infrastructure |
| dc-leaf-1 | DC-East | Arista vEOS | leaf | **Managed** | Customer compute leaf |
| dc-leaf-2 | DC-East | Arista vEOS | leaf | **Managed** | Customer compute leaf |
| dc-border-1 | DC-East | Arista vEOS | border-leaf | **Managed** | Customer fabric handoff |
| dc-border-2 | DC-East | Arista vEOS | border-leaf | **Managed** | Customer fabric handoff |
| dc-fw-1 | DC Security | FortiGate | firewall | **Managed** | Customer security (HA active) |
| dc-fw-2 | DC Security | FortiGate | firewall | **Managed** | Customer security (HA standby) |
| dc-ce-1 | DC-East | Cisco C8000v | CE | **Managed** | Customer WAN edge |
| dc-host-1 | DC-East | Alpine Linux | host | **Managed** | Customer workload |
| dc-host-2 | DC-East | Alpine Linux | host | **Managed** | Customer workload |
| br-ce-1 | Branch-01 | Cisco C8000v | CE | **Managed** | Customer branch edge |
| br-host-1 | Branch-01 | Alpine Linux | host | **Managed** | Customer workload |
| dr-leaf-1 | DR-West | Arista vEOS | leaf | **Managed** | Customer DR fabric |
| dr-leaf-2 | DR-West | Arista vEOS | leaf | **Managed** | Customer DR fabric |
| dr-fw-1 | DR Security | FortiGate | firewall | **Managed** | Customer security (HA active) |
| dr-fw-2 | DR Security | FortiGate | firewall | **Managed** | Customer security (HA standby) |
| dr-ce-1 | DR-West | Cisco C8000v | CE | **Managed** | Customer WAN edge |
| dr-host-1 | DR-West | Alpine Linux | host | **Managed** | Customer workload |
| sp-pe-1 | WAN | Cisco C8000v | PE | **Excluded** | Service provider — not customer equipment |
| sp-pe-2 | WAN | Cisco C8000v | PE | **Excluded** | Service provider — not customer equipment |

## Observed Interfaces

The agent monitors these specific interfaces in read-only mode for drift alerts:

| Device | Interface | Connects To | Purpose |
|--------|-----------|-------------|---------|
| dc-border-1 | Ethernet3 | dc-fw-1:port1 | Border-to-firewall handoff |
| dc-border-2 | Ethernet3 | dc-fw-2:port1 | Border-to-firewall handoff |
| dr-leaf-1 | Ethernet2 | dr-fw-1:port1 | Leaf-to-firewall handoff |
| dr-leaf-2 | Ethernet2 | dr-fw-2:port1 | Leaf-to-firewall handoff |

## Boundary Enforcement

The agent boundary is enforced in code at `agent/skills/fabric_health/skill.py`:

```python
def get_managed_devices(spec: dict) -> list[str]:
    return list(spec.get("agent", {}).get("boundary", {}).get("managed", []))
```

Every skill function calls `get_managed_devices()` and only connects to devices in the managed list. The agent **never** contacts excluded devices.
