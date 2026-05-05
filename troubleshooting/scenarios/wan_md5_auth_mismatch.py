"""Scenario: dc-ce-1 has TCP MD5 set toward sp-pe-1; sp-pe-1 doesn't.

`neighbor 172.16.0.1 password ...` on dc-ce-1 with no matching password
on sp-pe-1 means the TCP MD5 option mismatches. The TCP three-way handshake
never completes (or is reset), so the BGP FSM never reaches Established.
The neighbor sits in Active or Idle and the prefix counter is 0.

This is a *very* common real-world fault during password-rotation work
or PE migrations.
"""

from __future__ import annotations

from troubleshooting._common import REGISTRY, Scenario

DEVICE = "dc-ce-1"
PE_PRIMARY = "172.16.0.1"
PE_LOCAL_ASN = 65100
INJECTED_PASSWORD = "TS-WRONG-PW"


def inject(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {PE_LOCAL_ASN}",
            f"neighbor {PE_PRIMARY} password {INJECTED_PASSWORD}",
        ]
    )


def fix(conn: object) -> None:
    conn.send_config_set(
        [
            f"router bgp {PE_LOCAL_ASN}",
            f"no neighbor {PE_PRIMARY} password",
        ]
    )


def detect(conn: object) -> tuple[bool, str]:
    raw = conn.send_command("show ip bgp summary")
    for line in raw.splitlines():
        if not line.startswith(PE_PRIMARY):
            continue
        # State/PfxRcd is the last token
        last = line.split()[-1]
        # If the session is up, last is the prefix count (an int)
        try:
            int(last)
            return False, f"{PE_PRIMARY} session Established with {last} prefixes"
        except ValueError:
            return True, f"{PE_PRIMARY} BGP state is {last} — session not Established"
    return False, f"neighbor {PE_PRIMARY} not present in summary output"


SCENARIO = Scenario(
    name="wan-md5-auth-mismatch",
    device=DEVICE,
    platform="cisco_iosxe",
    difficulty="intermediate",
    summary="dc-ce-1 has a BGP password toward sp-pe-1 that sp-pe-1 doesn't share.",
    symptoms=(
        "BGP session to sp-pe-1 (172.16.0.1) cannot establish — stuck Active/Idle, "
        "MsgRcvd=0. Routes from sp-pe-1 are not in the table. sp-pe-2 is fine. "
        "All traffic is using the secondary path."
    ),
    runbook="troubleshooting/runbooks/wan_md5_auth_mismatch.md",
    inject=inject,
    detect=detect,
    fix=fix,
)

REGISTRY.register(SCENARIO)
