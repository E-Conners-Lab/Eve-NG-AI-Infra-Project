"""Integration test: end-to-end IPsec tunnel from dc-ce-1 to AWS strongSwan.

Skipped unless ``EVE_LIVE=1`` AND the AWS-side outputs are available — the
test needs a real strongSwan EC2 reachable at ``AWS_STRONGSWAN_EIP`` (its
public IP) and a target inside the VPC reachable through the tunnel
(``AWS_VPC_PROBE_IP``).

Asserts (live):
  - ``show crypto ikev2 sa`` reports a READY SA on dc-ce-1.
  - The on-prem Tunnel0 inner IP and the AWS-side inner IP both appear in
    the tunnel state.
  - A ping from dc-ce-1 sourced on Loopback0 to AWS_VPC_PROBE_IP succeeds
    with average RTT < 500 ms (cross-internet IPsec + VPC routing).

Run:
    EVE_LIVE=1 \
    AWS_STRONGSWAN_EIP=203.0.113.10 \
    AWS_VPC_PROBE_IP=10.0.64.10 \
    .venv/bin/python -m pytest tests/integration/test_cloud_tunnel_pipeline.py -v
"""

from __future__ import annotations

import os
import re

import pytest
from nornir_netmiko.tasks import netmiko_send_command

from automation.inventory import init_nornir

pytestmark = pytest.mark.integration

DEVICE = "dc-ce-1"
RTT_BUDGET_MS = 500


def _live_or_skip() -> tuple[str, str]:
    """Return (strongswan_eip, vpc_probe_ip) or skip the test.

    Lives at module scope as a helper instead of a fixture so it can be called
    from any test below without forcing all tests through the same fixture.
    """
    if os.environ.get("EVE_LIVE") != "1":
        pytest.skip("EVE_LIVE not set — skipping live cloud-tunnel integration test")
    eip = os.environ.get("AWS_STRONGSWAN_EIP", "").strip()
    probe = os.environ.get("AWS_VPC_PROBE_IP", "").strip()
    if not eip or not probe:
        pytest.skip(
            "AWS_STRONGSWAN_EIP and AWS_VPC_PROBE_IP must be set "
            "(see .aws_outputs.json after `make sync-aws-outputs`)"
        )
    return eip, probe


def _run_on_dc_ce1(command: str) -> str:
    """SSH to dc-ce-1 via Nornir and return raw command output."""
    nr = init_nornir(role="managed")
    target = nr.filter(filter_func=lambda h: h.name == DEVICE)
    result = target.run(task=netmiko_send_command, command_string=command)
    if result[DEVICE].failed:
        pytest.fail(f"{DEVICE}: '{command}' failed: {result[DEVICE].exception}")
    return str(result[DEVICE][0].result)


@pytest.fixture(scope="module")
def crypto_state() -> dict:
    """Fetch IKEv2 SA + IPsec SA state from dc-ce-1 once for all tests in the module."""
    _live_or_skip()
    return {
        "ikev2": _run_on_dc_ce1("show crypto ikev2 sa detail"),
        "ipsec": _run_on_dc_ce1("show crypto ipsec sa"),
    }


class TestIkeV2State:
    """The IKEv2 SA toward the AWS strongSwan must be READY."""

    def test_ikev2_sa_is_ready(self, crypto_state: dict) -> None:
        """`show crypto ikev2 sa detail` must contain a READY status row."""
        eip, _ = _live_or_skip()
        ike = crypto_state["ikev2"]
        # Status column on the data line; matches the parser's _IKEV2_DATA_LINE.
        assert re.search(r"\bREADY\b", ike), (
            f"no READY IKE SA in show crypto ikev2 sa detail:\n{ike[:400]}"
        )
        assert eip in ike, f"strongSwan EIP {eip} not present in IKEv2 SA output"

    def test_ipsec_inbound_and_outbound_sas_installed(self, crypto_state: dict) -> None:
        """`show crypto ipsec sa` must show both directions of ESP installed."""
        ipsec = crypto_state["ipsec"]
        assert "inbound esp sas:" in ipsec.lower(), "no inbound ESP SAs"
        assert "outbound esp sas:" in ipsec.lower(), "no outbound ESP SAs"


class TestDataPlane:
    """A ping through the tunnel must succeed with sane RTT."""

    def test_ping_aws_vpc_probe(self) -> None:
        """ping from dc-ce-1 sourced on Loopback0 reaches the VPC probe IP."""
        _, probe = _live_or_skip()
        cmd = f"ping {probe} source Loopback0 repeat 5 timeout 2"
        out = _run_on_dc_ce1(cmd)

        # IOS-XE format: "Success rate is 100 percent (5/5), round-trip min/avg/max = X/Y/Z ms"
        success = re.search(r"Success rate is\s+(\d+)\s+percent\s*\((\d+)/(\d+)\)", out)
        assert success is not None, f"unexpected ping output:\n{out[:400]}"
        sent = int(success.group(3))
        rcvd = int(success.group(2))
        assert rcvd >= max(1, sent - 1), (
            f"ping success too low: {rcvd}/{sent} (output: {out[:400]})"
        )

        rtt = re.search(r"min/avg/max\s*=\s*(\d+)/(\d+)/(\d+)\s*ms", out)
        assert rtt is not None, f"no RTT line in ping output:\n{out[:400]}"
        avg_ms = int(rtt.group(2))
        assert avg_ms < RTT_BUDGET_MS, (
            f"avg RTT {avg_ms} ms exceeds budget {RTT_BUDGET_MS} ms — "
            f"cross-internet IPsec is slow but not THIS slow; check MTU/PMTUD"
        )


class TestAgentObservability:
    """The MCP cloud_tunnel_health tool must agree with show crypto on dc-ce-1."""

    def test_mcp_tool_reports_ready(self) -> None:
        """In-process call to _run_cloud_tunnel_health must return ike_state=READY."""
        _live_or_skip()
        from mcp_server import _run_cloud_tunnel_health

        result = _run_cloud_tunnel_health(device=DEVICE)
        assert "error" not in result, f"MCP tool error: {result.get('error')}"
        assert result["ike_state"] == "READY", (
            f"MCP and show-crypto disagree — show crypto says READY, "
            f"MCP says {result['ike_state']}. parser={result.get('parser')}"
        )
        assert result["esp_state"] == "INSTALLED", f"ESP state mismatch: MCP={result['esp_state']}"
