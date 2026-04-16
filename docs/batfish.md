# Batfish Digital Twin

Batfish provides offline network config validation before anything touches live devices. It parses device configs, builds a network model, and answers questions about BGP, routing, reachability, and failure impact — all without SSH to a single device.

## Setup

### Docker Container
```bash
docker pull batfish/allinone
docker run -d --name batfish \
  -v batfish-data:/data \
  -p 8888:8888 -p 9997:9997 -p 9996:9996 \
  batfish/allinone
```

Ports: 9997 (API), 9996 (secondary), 8888 (Jupyter notebooks).

### Python Client
```bash
uv pip install pybatfish pandas
```

### Environment
Add to `.env`:
```
BATFISH_HOST=localhost
```

## Snapshot Structure

Batfish reads a snapshot directory with device configs and physical topology:

```
batfish/snapshot/
  configs/                       # 17 device .cfg files
    dc-spine-1.cfg               # Arista EOS
    dc-ce-1.cfg                  # Cisco IOS-XE
    dc-fw-1.cfg                  # FortiGate FortiOS
    ...
  batfish/
    layer1_topology.json         # Physical cabling (56 directed edges)
```

Built automatically from `configs/generated/` and `specs/generated/lab_spec.yaml`:
```bash
make build-snapshot
```

Host configs (Alpine Linux) are excluded — Batfish cannot parse them. Host topology edges are included so Batfish sees the full physical map.

## Validation Queries

### BGP Session Compatibility
```python
bf.q.bgpSessionCompatibility().answer().frame()
```
- **UNIQUE_MATCH**: underlay eBGP peer found and compatible
- **HALF_OPEN**: iBGP EVPN overlay session (depends on underlay — expected)
- **NO_REMOTE_AS / UNKNOWN_REMOTE**: configuration error — fails the gate

### BGP Session Status
```python
bf.q.bgpSessionStatus().answer().frame()
```
Shows ESTABLISHED vs NOT_ESTABLISHED for all modeled sessions.

### Traceroute Simulation
```python
from pybatfish.datamodel.flow import HeaderConstraints
bf.q.traceroute(
    startLocation='br-ce-1',
    headers=HeaderConstraints(srcIps='10.20.0.1', dstIps='10.1.0.1')
).answer().frame()
```

### Failure Impact Analysis
```python
bf.fork_snapshot(base_name='baseline', name='fail-spine1',
    deactivate_nodes=['dc-spine-1'], overwrite=True)
bf.q.differentialReachability(
    headers=HeaderConstraints(dstIps='10.1.0.0/16')
).answer(snapshot='fail-spine1', reference_snapshot='baseline').frame()
```

### Route Table Analysis
```python
bf.q.routes(nodes='dc-spine-1').answer().frame()
```

## Running Validation

```bash
# Full validation (fails on critical issues)
make validate-batfish

# Or directly:
python -m batfish.validate --strict
```

Pass/fail logic:
- **FAIL**: Any incompatible BGP session (not UNIQUE_MATCH or HALF_OPEN)
- **FAIL**: Any unreachable reachability matrix entry
- **WARN**: Route propagation issues (logged but don't block)

## Platform Coverage

| Platform | Config Parsing | BGP Analysis | Routing | Firewall Policy |
|----------|---------------|-------------|---------|-----------------|
| Arista EOS | Full | Full | Full | N/A |
| Cisco IOS-XE | Full | Full | Full | N/A |
| FortiGate FortiOS | Full | N/A (no BGP) | Partial | Full |

### EVPN/VXLAN
Batfish parses `address-family evpn` and VNI/VLAN config. iBGP EVPN sessions show as HALF_OPEN in session compatibility (they depend on underlay loopback reachability). This is expected — the agent's live `fabric_health` skill validates the overlay in real-time.

## Emergency Bypass

If Batfish blocks a valid deployment (e.g., pybatfish version change or parsing bug):

**CLI**: Skip the Batfish step and push directly:
```bash
make push-configs  # bypasses Batfish gate
```

**GitHub Actions**: Use the `skip_batfish` input on the deploy workflow. This is logged to the GAIT audit trail.

## Troubleshooting

```bash
# Check if Batfish is running
docker ps --filter name=batfish

# Restart Batfish
docker restart batfish

# Check logs
docker logs batfish --tail 20

# Test connection
python -c "from pybatfish.client.session import Session; Session(host='localhost'); print('OK')"
```

Note: On ARM64 Macs (Apple Silicon), Batfish runs under AMD64 emulation. Startup takes 2-3 minutes and queries are slower than on native AMD64.
