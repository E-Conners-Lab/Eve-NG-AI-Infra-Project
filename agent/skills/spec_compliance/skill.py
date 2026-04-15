"""Spec Compliance Skill.

Compares declared state (YAML spec) against live device state for each
managed device. Reports exact diffs when drift is detected.
"""

from __future__ import annotations


def compare_interfaces(
    device_name: str, device_spec: dict, live_interfaces: dict[str, str]
) -> list[dict]:
    """Compare spec interface IPs against live interface IPs.

    Args:
        device_name: Name of the device being checked.
        device_spec: Device dict from the YAML spec (with interfaces list).
        live_interfaces: Dict of {interface_name: ip_address} from live device.

    Returns:
        List of drift dicts. Empty list = no drift.
    """
    drifts: list[dict] = []

    for iface in device_spec.get("interfaces", []):
        iface_name = iface["name"]
        expected_ip = iface.get("ipv4", "")

        if not expected_ip:
            continue

        live_ip = live_interfaces.get(iface_name, "")

        if not live_ip:
            drifts.append(
                {
                    "device": device_name,
                    "interface": iface_name,
                    "field": "ipv4",
                    "expected": expected_ip,
                    "live": "MISSING — interface not found in live state",
                }
            )
        elif live_ip != expected_ip:
            drifts.append(
                {
                    "device": device_name,
                    "interface": iface_name,
                    "field": "ipv4",
                    "expected": expected_ip,
                    "live": live_ip,
                }
            )

    return drifts


def compare_asn(device_name: str, expected_asn: int, live_asn: int) -> dict | None:
    """Compare expected ASN against live ASN.

    Returns None if they match, or a drift dict if they differ.
    """
    if expected_asn == live_asn:
        return None
    return {
        "device": device_name,
        "field": "asn",
        "expected": expected_asn,
        "live": live_asn,
    }
