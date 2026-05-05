"""Tests for Batfish snapshot builder — TDD.

Written BEFORE the implementation. Validates topology edge extraction,
layer1_topology.json generation, and snapshot directory structure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "generated" / "lab_spec.yaml"
CONFIGS_DIR = Path(__file__).parent.parent.parent / "configs" / "generated"


@pytest.fixture
def spec() -> dict:
    """Load the full lab spec."""
    return yaml.safe_load(SPEC_PATH.read_text())


# ---------------------------------------------------------------------------
# Topology edge extraction
# ---------------------------------------------------------------------------
class TestTopologyEdges:
    """build_topology_edges must extract all physical links from the spec."""

    def test_total_unique_physical_links(self, spec: dict) -> None:
        """Must find exactly 26 unique physical links (deduplicated).

        Includes 2 fw↔fw heartbeat links (ha-link type). Hosts are excluded
        from the lab.
        """
        from batfish.snapshot import build_topology_edges

        edges = build_topology_edges(spec)
        # Each physical link produces 2 directed edges (A->B and B->A)
        # 26 physical links = 52 directed edges
        assert len(edges) == 52

    def test_edges_are_bidirectional(self, spec: dict) -> None:
        """Every A->B edge must have a matching B->A edge."""
        from batfish.snapshot import build_topology_edges

        edges = build_topology_edges(spec)
        edge_set = {
            (
                e["node1"]["hostname"],
                e["node1"]["interfaceName"],
                e["node2"]["hostname"],
                e["node2"]["interfaceName"],
            )
            for e in edges
        }
        for e in edges:
            reverse = (
                e["node2"]["hostname"],
                e["node2"]["interfaceName"],
                e["node1"]["hostname"],
                e["node1"]["interfaceName"],
            )
            assert reverse in edge_set, (
                f"Missing reverse edge for {e['node1']['hostname']}:{e['node1']['interfaceName']}"
            )

    def test_includes_fw_heartbeat_links(self, spec: dict) -> None:
        """fw↔fw HA heartbeat links (port3) must be in the topology."""
        from batfish.snapshot import build_topology_edges

        edges = build_topology_edges(spec)
        edge_pairs = {(e["node1"]["hostname"], e["node2"]["hostname"]) for e in edges}
        assert ("dc-fw-1", "dc-fw-2") in edge_pairs or ("dc-fw-2", "dc-fw-1") in edge_pairs
        assert ("dr-fw-1", "dr-fw-2") in edge_pairs or ("dr-fw-2", "dr-fw-1") in edge_pairs

    def test_includes_cross_section_peers(self, spec: dict) -> None:
        """Links between sites and security zones must be found (e.g., border->fw)."""
        from batfish.snapshot import build_topology_edges

        edges = build_topology_edges(spec)
        edge_pairs = {(e["node1"]["hostname"], e["node2"]["hostname"]) for e in edges}
        # Border to firewall (site -> security)
        assert ("dc-border-1", "dc-fw-1") in edge_pairs or ("dc-fw-1", "dc-border-1") in edge_pairs
        # CE to PE (site -> wan_transport)
        assert ("dc-ce-1", "sp-pe-1") in edge_pairs or ("sp-pe-1", "dc-ce-1") in edge_pairs

    def test_spine_leaf_fabric_edges(self, spec: dict) -> None:
        """DC-East fabric must have all spine-leaf P2P links."""
        from batfish.snapshot import build_topology_edges

        edges = build_topology_edges(spec)
        edge_pairs = {(e["node1"]["hostname"], e["node2"]["hostname"]) for e in edges}
        # Each leaf/border connects to both spines
        for leaf in ("dc-leaf-1", "dc-leaf-2", "dc-border-1", "dc-border-2"):
            for spine in ("dc-spine-1", "dc-spine-2"):
                assert (leaf, spine) in edge_pairs or (spine, leaf) in edge_pairs, (
                    f"Missing link: {leaf} <-> {spine}"
                )


# ---------------------------------------------------------------------------
# Layer 1 topology JSON
# ---------------------------------------------------------------------------
class TestLayer1Topology:
    """build_layer1_topology must produce valid Batfish JSON format."""

    def test_json_structure(self, spec: dict) -> None:
        """Must have top-level 'edges' key containing a list."""
        from batfish.snapshot import build_layer1_topology

        topo = build_layer1_topology(spec)
        assert "edges" in topo
        assert isinstance(topo["edges"], list)

    def test_edge_format(self, spec: dict) -> None:
        """Each edge must have node1/node2 with hostname and interfaceName."""
        from batfish.snapshot import build_layer1_topology

        topo = build_layer1_topology(spec)
        for edge in topo["edges"]:
            assert "node1" in edge
            assert "node2" in edge
            assert "hostname" in edge["node1"]
            assert "interfaceName" in edge["node1"]
            assert "hostname" in edge["node2"]
            assert "interfaceName" in edge["node2"]

    def test_edge_count_matches(self, spec: dict) -> None:
        """Edge count must equal 52 (26 physical links * 2 directions)."""
        from batfish.snapshot import build_layer1_topology

        topo = build_layer1_topology(spec)
        assert len(topo["edges"]) == 52


# ---------------------------------------------------------------------------
# Snapshot directory structure
# ---------------------------------------------------------------------------
class TestSnapshotBuild:
    """build_snapshot must create the correct directory layout for Batfish."""

    def test_creates_configs_directory(self, tmp_path: Path, spec: dict) -> None:
        """snapshot/configs/ must exist and contain .cfg files."""
        from batfish.snapshot import build_snapshot

        snapshot_dir = build_snapshot(spec, CONFIGS_DIR, tmp_path / "snapshot")
        configs_path = snapshot_dir / "configs"
        assert configs_path.is_dir()
        cfg_files = list(configs_path.glob("*.cfg"))
        assert len(cfg_files) == 17  # All network devices, no hosts

    def test_excludes_host_configs(self, tmp_path: Path, spec: dict) -> None:
        """Host configs (Alpine Linux) must NOT be in the snapshot."""
        from batfish.snapshot import build_snapshot

        snapshot_dir = build_snapshot(spec, CONFIGS_DIR, tmp_path / "snapshot")
        configs_path = snapshot_dir / "configs"
        cfg_names = {f.stem for f in configs_path.glob("*.cfg")}
        hosts = {"dc-host-1", "dc-host-2", "br-host-1", "dr-host-1"}
        assert cfg_names.isdisjoint(hosts), f"Host configs found in snapshot: {cfg_names & hosts}"

    def test_creates_layer1_topology_json(self, tmp_path: Path, spec: dict) -> None:
        """snapshot/batfish/layer1_topology.json must exist and be valid JSON."""
        from batfish.snapshot import build_snapshot

        snapshot_dir = build_snapshot(spec, CONFIGS_DIR, tmp_path / "snapshot")
        topo_path = snapshot_dir / "batfish" / "layer1_topology.json"
        assert topo_path.is_file()
        topo = json.loads(topo_path.read_text())
        assert "edges" in topo
        assert len(topo["edges"]) == 52

    def test_clears_stale_files_on_rebuild(self, tmp_path: Path, spec: dict) -> None:
        """Rebuilding must clear stale files from previous run."""
        from batfish.snapshot import build_snapshot

        out = tmp_path / "snapshot"
        # First build
        build_snapshot(spec, CONFIGS_DIR, out)
        # Plant a stale file
        stale = out / "configs" / "stale-device.cfg"
        stale.write_text("stale config")
        assert stale.exists()
        # Rebuild — stale file must be gone
        build_snapshot(spec, CONFIGS_DIR, out)
        assert not stale.exists()

    def test_returns_snapshot_path(self, tmp_path: Path, spec: dict) -> None:
        """Must return the Path to the snapshot directory."""
        from batfish.snapshot import build_snapshot

        result = build_snapshot(spec, CONFIGS_DIR, tmp_path / "snapshot")
        assert isinstance(result, Path)
        assert result.is_dir()
