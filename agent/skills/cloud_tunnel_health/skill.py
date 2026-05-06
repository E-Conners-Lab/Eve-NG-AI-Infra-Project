"""Cloud Tunnel Health Skill.

Hand-rolled parsers for `show crypto ikev2 sa detail` and `show crypto ipsec sa`
on Cisco IOS-XE. ntc-templates does not ship cisco_ios templates for these
commands at the pinned version, so we parse manually with focused regex.

Both parsers are total — they accept malformed/empty input and return either
an empty list (`parse_ikev2_sa_detail`) or sentinel "UNKNOWN" state
(`parse_ipsec_sa`) rather than raising. Callers (the MCP tool) translate
that into the "UNKNOWN" defensive return contract.
"""

from __future__ import annotations

import re

# Parses the tabular data line under the "IPv4 Crypto IKEv2 SA" header:
#   "1   172.16.0.101/500   203.0.113.10/500   none/none   READY"
_IKEV2_DATA_LINE = re.compile(
    r"^\s*(?P<tunnel_id>\d+)\s+"
    r"(?P<local>\S+?)/(?P<local_port>\d+)\s+"
    r"(?P<peer>\S+?)/(?P<peer_port>\d+)\s+"
    r"\S+\s+"  # vrf column (e.g. "none/none")
    r"(?P<status>\w+)\s*$"
)


def parse_ikev2_sa_detail(raw: str) -> list[dict]:
    """Parse `show crypto ikev2 sa detail` output into a list of tunnel dicts.

    Returns an empty list if the output is empty, garbled, or contains no
    parseable SA entries. The caller treats `[]` as "UNKNOWN" rather than
    "DOWN" — the difference matters for alerting (a check-failure should
    not page on-call as a tunnel-down).
    """
    if not raw or not raw.strip():
        return []

    tunnels: list[dict] = []
    lines = raw.splitlines()

    i = 0
    while i < len(lines):
        match = _IKEV2_DATA_LINE.match(lines[i])
        if not match:
            i += 1
            continue

        tunnel: dict = {
            "tunnel_id": int(match.group("tunnel_id")),
            "local": match.group("local"),
            "local_port": int(match.group("local_port")),
            "peer": match.group("peer"),
            "peer_port": int(match.group("peer_port")),
            "ike_state": match.group("status"),
        }

        # Walk the indented attribute lines that follow until the next data line
        # or the end of the output. Skip everything we don't recognize.
        j = i + 1
        while j < len(lines):
            line = lines[j]
            if _IKEV2_DATA_LINE.match(line):
                break

            encr_m = re.search(r"Encr:\s*([\w-]+)", line)
            if encr_m:
                tunnel["encryption"] = encr_m.group(1)

            dh_m = re.search(r"DH Grp:\s*(\d+)", line)
            if dh_m:
                tunnel["dh_group"] = int(dh_m.group(1))

            life_m = re.search(r"Life/Active Time:\s*(\d+)/(\d+)\s*sec", line)
            if life_m:
                tunnel["lifetime_sec"] = int(life_m.group(1))
                tunnel["active_sec"] = int(life_m.group(2))

            j += 1

        tunnels.append(tunnel)
        i = j

    return tunnels


def parse_ipsec_sa(raw: str) -> dict:
    """Parse `show crypto ipsec sa` output into structured counters and ESP state.

    Returns a dict with `esp_state` ('INSTALLED', 'PARTIAL', 'DOWN', or 'UNKNOWN'),
    `encrypted_packets`, and `decrypted_packets`. Empty input yields `UNKNOWN`
    rather than `DOWN` to preserve the check-vs-tunnel distinction.
    """
    if not raw or not raw.strip():
        return {"esp_state": "UNKNOWN", "encrypted_packets": 0, "decrypted_packets": 0}

    encrypt_m = re.search(r"#pkts encrypt:\s*(\d+)", raw)
    decrypt_m = re.search(r"#pkts decrypt:\s*(\d+)", raw)

    encrypted = int(encrypt_m.group(1)) if encrypt_m else 0
    decrypted = int(decrypt_m.group(1)) if decrypt_m else 0

    has_inbound = bool(re.search(r"inbound esp sas:\s*\n\s*spi:", raw))
    has_outbound = bool(re.search(r"outbound esp sas:\s*\n\s*spi:", raw))

    if has_inbound and has_outbound:
        esp_state = "INSTALLED"
    elif has_inbound or has_outbound:
        esp_state = "PARTIAL"
    else:
        esp_state = "DOWN"

    return {
        "esp_state": esp_state,
        "encrypted_packets": encrypted,
        "decrypted_packets": decrypted,
    }
