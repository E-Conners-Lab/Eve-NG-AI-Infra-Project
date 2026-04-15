"""Tests for YAML spec schema validation.

TDD: These tests are written BEFORE the spec is finalized.
They define what a valid spec looks like and catch structural regressions.
"""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError, validate


@pytest.fixture
def schema() -> dict:
    """Load the JSON Schema from disk."""
    schema_path = Path(__file__).parent.parent.parent / "specs" / "schema.json"
    return json.loads(schema_path.read_text())


@pytest.fixture
def minimal_valid_spec() -> dict:
    """Return the smallest spec that should pass validation."""
    return {
        "metadata": {
            "version": "1.0.0",
            "generated_from": "netbox",
            "generated_at": "2026-04-14T00:00:00Z",
            "schema_version": "1.0.0",
        },
        "sites": {
            "dc_east": {
                "devices": [
                    {
                        "name": "dc-spine-1",
                        "role": "spine",
                        "platform": "arista_eos",
                    }
                ]
            }
        },
        "agent": {
            "boundary": {
                "managed": ["dc-spine-1"],
                "observed": [],
                "excluded": [],
            }
        },
    }


# --- Schema self-consistency ---


class TestSchemaValidity:
    """Verify the schema itself is well-formed."""

    def test_schema_is_valid_json_schema(self, schema: dict) -> None:
        """The schema file must be a valid JSON Schema Draft 2020-12."""
        Draft202012Validator.check_schema(schema)

    def test_schema_has_required_top_level_keys(self, schema: dict) -> None:
        """Schema must require metadata, sites, and agent at the top level."""
        assert set(schema["required"]) == {"metadata", "sites", "agent"}


# --- Positive validation (valid specs pass) ---


class TestValidSpecs:
    """Specs that conform to the schema must pass validation."""

    def test_minimal_spec_passes(self, schema: dict, minimal_valid_spec: dict) -> None:
        """The smallest valid spec must validate without errors."""
        validate(instance=minimal_valid_spec, schema=schema)

    def test_full_spec_with_all_sections(self, schema: dict, minimal_valid_spec: dict) -> None:
        """A spec with all optional sections populated must validate."""
        full_spec = {
            **minimal_valid_spec,
            "wan_transport": {
                "devices": [{"name": "sp-pe-1", "role": "pe", "platform": "cisco_iosxe"}],
                "links": [
                    {
                        "a_end": {"device": "sp-pe-1", "interface": "GigabitEthernet1"},
                        "z_end": {"device": "sp-pe-2", "interface": "GigabitEthernet1"},
                        "type": "transport",
                    }
                ],
            },
            "security": {
                "dc": {
                    "firewalls": [
                        {"name": "dc-fw-1", "role": "firewall", "platform": "fortinet_fortios"}
                    ],
                    "zones": [
                        {"name": "dc-trust", "type": "trust", "interfaces": ["port1"]},
                        {"name": "wan-untrust", "type": "untrust", "interfaces": ["port2"]},
                    ],
                    "policies": [
                        {
                            "name": "allow-dc-to-wan",
                            "source_zone": "dc-trust",
                            "destination_zone": "wan-untrust",
                            "action": "allow",
                            "services": ["tcp/443", "tcp/80"],
                        }
                    ],
                }
            },
            "tests": {
                "reachability_matrix": [
                    {
                        "source": "dc-host-1",
                        "destination": "dc-host-2",
                        "description": "East-west inter-VNI through fabric",
                    }
                ]
            },
        }
        validate(instance=full_spec, schema=schema)

    def test_spec_with_multiple_sites(self, schema: dict, minimal_valid_spec: dict) -> None:
        """A spec with dc_east, branch_01, and dr_west must validate."""
        multi_site = {
            **minimal_valid_spec,
            "sites": {
                **minimal_valid_spec["sites"],
                "branch_01": {
                    "devices": [{"name": "br-ce-1", "role": "ce", "platform": "cisco_iosxe"}]
                },
                "dr_west": {
                    "devices": [{"name": "dr-leaf-1", "role": "leaf", "platform": "arista_eos"}]
                },
            },
        }
        validate(instance=multi_site, schema=schema)

    def test_spec_with_agent_thresholds(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Agent thresholds must validate when present."""
        spec = {
            **minimal_valid_spec,
            "agent": {
                **minimal_valid_spec["agent"],
                "thresholds": {
                    "bgp_hold_time": 180,
                    "interface_flap_window_sec": 300,
                    "max_route_deviation_pct": 10.0,
                },
            },
        }
        validate(instance=spec, schema=schema)


# --- Negative validation (invalid specs fail) ---


class TestInvalidSpecs:
    """Specs violating the schema must be rejected."""

    def test_missing_metadata_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """A spec without metadata must fail validation."""
        del minimal_valid_spec["metadata"]
        with pytest.raises(ValidationError, match="'metadata' is a required property"):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_missing_sites_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """A spec without sites must fail validation."""
        del minimal_valid_spec["sites"]
        with pytest.raises(ValidationError, match="'sites' is a required property"):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_missing_agent_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """A spec without agent must fail validation."""
        del minimal_valid_spec["agent"]
        with pytest.raises(ValidationError, match="'agent' is a required property"):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_empty_managed_list_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Agent boundary must have at least one managed device."""
        minimal_valid_spec["agent"]["boundary"]["managed"] = []
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_invalid_generated_from_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """generated_from must be 'netbox' or 'manual'."""
        minimal_valid_spec["metadata"]["generated_from"] = "hand_typed"
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_invalid_schema_version_format_fails(
        self, schema: dict, minimal_valid_spec: dict
    ) -> None:
        """schema_version must match semver pattern."""
        minimal_valid_spec["metadata"]["schema_version"] = "v1"
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_empty_device_name_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Device name must not be empty."""
        minimal_valid_spec["sites"]["dc_east"]["devices"][0]["name"] = ""
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_invalid_device_role_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Device role must be from the allowed enum."""
        minimal_valid_spec["sites"]["dc_east"]["devices"][0]["role"] = "router"
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_invalid_platform_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Device platform must be from the allowed enum."""
        minimal_valid_spec["sites"]["dc_east"]["devices"][0]["platform"] = "juniper_junos"
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_extra_top_level_key_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Additional top-level keys must be rejected."""
        minimal_valid_spec["unsupported_section"] = {}
        with pytest.raises(ValidationError, match="Additional properties"):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_no_devices_in_site_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Sites must have at least one device."""
        minimal_valid_spec["sites"]["dc_east"]["devices"] = []
        with pytest.raises(ValidationError):
            validate(instance=minimal_valid_spec, schema=schema)

    def test_invalid_firewall_action_fails(self, schema: dict, minimal_valid_spec: dict) -> None:
        """Security policy action must be allow, deny, or inspect."""
        spec = {
            **minimal_valid_spec,
            "security": {
                "dc": {
                    "policies": [
                        {
                            "name": "bad-policy",
                            "source_zone": "trust",
                            "destination_zone": "untrust",
                            "action": "drop",
                        }
                    ]
                }
            },
        }
        with pytest.raises(ValidationError):
            validate(instance=spec, schema=schema)


# --- Generated spec file validation ---


class TestGeneratedSpecFile:
    """Validate the actual generated spec files on disk."""

    def test_generated_spec_exists(self, specs_dir: Path) -> None:
        """At least one generated spec file must exist."""
        yaml_files = list(specs_dir.glob("*.yaml")) + list(specs_dir.glob("*.yml"))
        assert len(yaml_files) > 0, f"No YAML spec files found in {specs_dir}"

    def test_generated_specs_validate_against_schema(self, schema: dict, specs_dir: Path) -> None:
        """Every generated spec file must pass schema validation."""
        yaml_files = list(specs_dir.glob("*.yaml")) + list(specs_dir.glob("*.yml"))
        for spec_file in yaml_files:
            spec = yaml.safe_load(spec_file.read_text())
            validate(instance=spec, schema=schema)
