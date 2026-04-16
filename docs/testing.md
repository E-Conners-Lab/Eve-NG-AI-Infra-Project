# Testing Guide

## Test Pyramid

```
                    ┌─────────────┐
                    │   Chaos     │  Fault injection on live devices
                    │   Tests     │  make chaos-test
                    ├─────────────┤
                 ┌──┤ Integration │  Live EVE-NG + NetBox + Batfish
                 │  │   Tests     │  make test-integration
                 │  ├─────────────┤
              ┌──┤  │  Batfish    │  Offline config validation
              │  │  │  Validation │  make validate-batfish
              │  │  ├─────────────┤
           ┌──┤  │  │    Unit     │  Mocked everything, fast
           │  │  │  │    Tests    │  make test
           └──┴──┴──┴─────────────┘
```

## Running Tests

### Unit Tests (199 tests, ~4 seconds)
```bash
make test
# or: pytest tests/ --ignore=tests/integration -v
```

No live infrastructure needed. All device interactions mocked. Covers:
- Schema validation (20 tests)
- NetBox generator with mocked API (30+ tests)
- Config generation from Jinja2 templates (40+ tests)
- Skill parsers: BGP summary, interface IPs, HA standby (22 tests)
- Cross-device consistency (18 tests)
- Batfish snapshot builder (13 tests)
- Batfish validation queries with mocked pybatfish (16 tests)
- NetBox reconciler with mocked pynetbox (21 tests)
- Chaos test logic: fault selection, commands, detection (18 tests)
- Diagnostic script analysis (5 tests)

### Batfish Validation (requires Docker)
```bash
# Start Batfish if not running
docker start batfish || docker run -d --name batfish \
  -v batfish-data:/data -p 8888:8888 -p 9997:9997 -p 9996:9996 \
  batfish/allinone

# Run validation
make validate-batfish
```

Builds a snapshot from `configs/generated/` and `specs/generated/lab_spec.yaml`, uploads to Batfish, and checks:
- BGP session compatibility (32 underlay sessions)
- Reachability matrix (6 host-to-host paths)
- Failure impact analysis

### Integration Tests (8 tests, requires live lab)
```bash
make test-integration
# or: pytest tests/integration/ -v
```

Requires:
- EVE-NG with all 21 nodes running
- NetBox at localhost:8000
- Batfish Docker container
- `.env` with all credentials

Tests:
1. Batfish snapshot loads all 17 configs
2. All underlay BGP sessions compatible
3. Corrupted config detected by Batfish gate
4. fabric_health: all BGP sessions established
5. branch_connectivity: all CE dual-homed sessions up
6. spec_compliance: zero drift across all managed devices
7. BGP break detection with auto-rollback
8. Batfish failure impact simulation

### Chaos Tests (requires live lab)
```bash
make chaos-test
# or: python -m scripts.chaos_test --dry-run  (preview only)
# or: python -m scripts.chaos_test --seed 42  (reproducible)
# or: python -m scripts.chaos_test --device dc-spine-1  (target specific)
```

Injects a random fault into a managed device, runs the agent to verify detection, then rolls back. Fault types per platform:

| Platform | Faults |
|----------|--------|
| Arista | shut_interface, wrong_asn, remove_vni, change_ip |
| Cisco | shut_interface, wrong_asn, change_ip |
| FortiGate | shut_interface, change_ip |

## CI/CD

Tests run automatically in GitHub Actions:
- **Push/PR**: lint + schema + generator + config-gen + unit-tests + batfish
- **Deploy**: all CI + batfish-gate + push + reachability + agent validation
