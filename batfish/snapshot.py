"""Batfish snapshot builder.

Creates the directory structure Batfish expects from the project's
generated configs and YAML spec topology data. Pure functions — no
network calls, no live devices.

Snapshot layout::

    snapshot/
      configs/                  # Arista/Cisco/FortiGate .cfg files
      batfish/
        layer1_topology.json    # Physical cabling (bidirectional edges)

Usage:
    python -m batfish.snapshot
    python -m batfish.snapshot --output /tmp/snapshot
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "generated"
DEFAULT_OUTPUT = Path(__file__).parent.parent / "batfish" / "snapshot"

# Platforms whose configs Batfish can parse
BATFISH_SUPPORTED_PLATFORMS = {"arista_eos", "cisco_iosxe", "fortinet_fortios"}


def _iter_all_devices(spec: dict) -> Iterator[dict]:
    """Yield every device across sites, wan_transport, and security sections."""
    for _site_key, site in spec.get("sites", {}).items():
        for dev in site.get("devices", []):
            yield dev
    for dev in spec.get("wan_transport", {}).get("devices", []):
        yield dev
    for _zone_key, zone in spec.get("security", {}).items():
        for dev in zone.get("firewalls", []):
            yield dev


def build_topology_edges(spec: dict) -> list[dict]:
    """Extract all physical links from the spec as bidirectional Batfish edges.

    Walks every device's interfaces looking for ``peer`` and ``peer_interface``
    fields. Each physical link produces two directed edges (A→B and B→A) as
    Batfish requires.

    Includes host-to-switch links so Batfish sees the full physical topology,
    even though host configs are not included in the snapshot.

    Returns a list of edge dicts in Batfish ``layer1_topology.json`` format.
    """
    # Collect unique undirected edges using frozenset keys for dedup
    seen: set[frozenset[tuple[str, str]]] = set()
    edges: list[dict] = []

    for dev in _iter_all_devices(spec):
        for iface in dev.get("interfaces", []):
            peer = iface.get("peer")
            peer_iface = iface.get("peer_interface")
            if not peer or not peer_iface:
                continue

            local = (dev["name"], iface["name"])
            remote = (peer, peer_iface)
            key = frozenset((local, remote))

            if key in seen:
                continue
            seen.add(key)

            # Batfish requires both directions
            edges.append(
                {
                    "node1": {"hostname": local[0], "interfaceName": local[1]},
                    "node2": {"hostname": remote[0], "interfaceName": remote[1]},
                }
            )
            edges.append(
                {
                    "node1": {"hostname": remote[0], "interfaceName": remote[1]},
                    "node2": {"hostname": local[0], "interfaceName": local[1]},
                }
            )

    return edges


def build_layer1_topology(spec: dict) -> dict:
    """Build the Batfish ``layer1_topology.json`` structure from the spec."""
    return {"edges": build_topology_edges(spec)}


def build_snapshot(spec: dict, configs_dir: Path, output_dir: Path) -> Path:
    """Create a complete Batfish snapshot directory.

    Clears ``output_dir`` if it exists (prevents stale files from previous
    runs), copies all network device configs into ``configs/``, and generates
    ``batfish/layer1_topology.json`` from the spec.

    Host configs (Alpine Linux) are excluded — Batfish cannot parse them.

    Returns the path to the snapshot directory.
    """
    # Clear previous snapshot
    if output_dir.exists():
        shutil.rmtree(output_dir)

    # Create directory structure
    snapshot_configs = output_dir / "configs"
    snapshot_batfish = output_dir / "batfish"
    snapshot_configs.mkdir(parents=True, exist_ok=True)
    snapshot_batfish.mkdir(parents=True, exist_ok=True)

    # Copy network device configs (skip hosts)
    host_names = _get_host_names(spec)
    for cfg_file in sorted(configs_dir.glob("*.cfg")):
        device_name = cfg_file.stem
        if device_name not in host_names:
            shutil.copy2(cfg_file, snapshot_configs / cfg_file.name)

    # Generate layer 1 topology
    topo = build_layer1_topology(spec)
    topo_path = snapshot_batfish / "layer1_topology.json"
    topo_path.write_text(json.dumps(topo, indent=2))

    return output_dir


def _get_host_names(spec: dict) -> set[str]:
    """Return the set of host device names (role=host or platform=linux)."""
    hosts: set[str] = set()
    for dev in _iter_all_devices(spec):
        if dev.get("role") == "host" or dev.get("platform") == "linux":
            hosts.add(dev["name"])
    return hosts


def main() -> None:
    """CLI entrypoint — build a Batfish snapshot from spec + generated configs."""
    parser = argparse.ArgumentParser(description="Build Batfish snapshot")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--configs", type=Path, default=CONFIGS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.spec.exists():
        print(f"ERROR: Spec not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    spec = yaml.safe_load(args.spec.read_text())
    snapshot_dir = build_snapshot(spec, args.configs, args.output)

    topo = json.loads((snapshot_dir / "batfish" / "layer1_topology.json").read_text())
    cfg_count = len(list((snapshot_dir / "configs").glob("*.cfg")))
    edge_count = len(topo["edges"])

    print(f"Batfish snapshot built: {snapshot_dir}")
    print(f"  Configs: {cfg_count} devices")
    print(f"  Topology: {edge_count} directed edges ({edge_count // 2} physical links)")


if __name__ == "__main__":
    main()
