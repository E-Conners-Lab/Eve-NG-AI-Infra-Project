"""Live TDD red/green demo against the AS-path prepending policy on dr-ce-1.

Sequence:
  1. Regress dr-ce-1 — remove the LONG-PATH-OUT route-map binding
  2. Run pytest — expect RED (the prepending test fails)
  3. Reapply the route-map binding
  4. Run pytest — expect GREEN

Designed for screencast / LinkedIn capture: a single command, clear banners
between phases, total runtime ~30 seconds.

Usage:
    python -m automation.demos.tdd_red_green
"""

from __future__ import annotations

import subprocess
import sys
import time

from netmiko import ConnectHandler

from scripts.credentials import load_credentials

DR_CE_1_IP = "192.168.68.129"
SECONDARY_PE_NEIGHBOR_IP = "172.16.0.12"  # sp-pe-2 from dr-ce-1's perspective
TEST_TARGET = "tests/integration/test_dr_ce1_aspath_prepend.py"
ROUTE_REFRESH_WAIT_SEC = 5


def _banner(text: str, char: str = "=") -> None:
    line = char * 72
    print(f"\n{line}\n  {text}\n{line}")


def _connect_dr_ce_1():
    creds = load_credentials()
    return ConnectHandler(
        device_type="cisco_xe",
        host=DR_CE_1_IP,
        username=creds.device_username,
        password=creds.device_password,
        secret=creds.device_password,
    )


def _bgp_config(commands: list[str]) -> None:
    """Apply a small BGP config delta on dr-ce-1, then route-refresh outbound."""
    c = _connect_dr_ce_1()
    c.enable()
    c.send_config_set(commands)
    c.send_command(f"clear ip bgp {SECONDARY_PE_NEIGHBOR_IP} soft out", read_timeout=30)
    c.disconnect()


def regress_to_pre_policy_state() -> None:
    print(f"Removing 'neighbor {SECONDARY_PE_NEIGHBOR_IP} route-map LONG-PATH-OUT out'...")
    _bgp_config(
        [
            "router bgp 65130",
            " address-family ipv4",
            f"  no neighbor {SECONDARY_PE_NEIGHBOR_IP} route-map LONG-PATH-OUT out",
            " exit-address-family",
        ]
    )
    print(f"Triggered route-refresh out toward {SECONDARY_PE_NEIGHBOR_IP}.")


def apply_policy() -> None:
    print(f"Adding 'neighbor {SECONDARY_PE_NEIGHBOR_IP} route-map LONG-PATH-OUT out'...")
    _bgp_config(
        [
            "router bgp 65130",
            " address-family ipv4",
            f"  neighbor {SECONDARY_PE_NEIGHBOR_IP} route-map LONG-PATH-OUT out",
            " exit-address-family",
        ]
    )
    print(f"Triggered route-refresh out toward {SECONDARY_PE_NEIGHBOR_IP}.")


def run_pytest(expect_pass: bool) -> bool:
    """Run the prepending test. Return True iff result matches expectation."""
    label = "GREEN (expect PASS)" if expect_pass else "RED (expect FAIL)"
    print(f"\nRunning pytest — {label}\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TEST_TARGET,
            "-m",
            "integration",
            "-v",
            "--no-header",
            "--tb=line",
        ]
    )
    return (result.returncode == 0) == expect_pass


def main() -> int:
    _banner("TDD red→green demo: AS-path prepending on dr-ce-1")
    print(f"Test under exercise: {TEST_TARGET}")
    print("Each phase modifies one BGP route-map binding and re-runs the test.")

    _banner("PHASE 1 — regress to pre-policy state", "-")
    regress_to_pre_policy_state()
    print(f"Waiting {ROUTE_REFRESH_WAIT_SEC}s for BGP convergence...")
    time.sleep(ROUTE_REFRESH_WAIT_SEC)

    _banner("PHASE 2 — RED", "-")
    red_correct = run_pytest(expect_pass=False)

    _banner("PHASE 3 — apply policy", "-")
    apply_policy()
    print(f"Waiting {ROUTE_REFRESH_WAIT_SEC}s for BGP convergence...")
    time.sleep(ROUTE_REFRESH_WAIT_SEC)

    _banner("PHASE 4 — GREEN", "-")
    green_correct = run_pytest(expect_pass=True)

    _banner("RESULT")
    if red_correct and green_correct:
        print("✓ Full red → green TDD cycle completed as expected.")
        return 0
    print("✗ Cycle did not complete cleanly:")
    print(f"  RED phase result expected: pytest fails — got match: {red_correct}")
    print(f"  GREEN phase result expected: pytest passes — got match: {green_correct}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
