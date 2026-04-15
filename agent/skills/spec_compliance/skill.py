"""Spec Compliance Skill.

Compares declared state (YAML spec) against live device state for each
managed device. Reports exact diffs when drift is detected.
"""

from __future__ import annotations

import re


def _mask_to_prefix(dotted_mask: str) -> int:
    """Convert dotted subnet mask to prefix length. e.g. 255.255.255.254 → 31."""
    try:
        octets = [int(o) for o in dotted_mask.split(".")]
        bits = "".join(f"{o:08b}" for o in octets)
        return bits.count("1")
    except (ValueError, AttributeError):
        return 0


def parse_eos_interfaces(output: str) -> dict[str, str]:
    """Parse Arista EOS 'show ip interface brief' — IPs in CIDR notation.

    Example line: ``Ethernet1   10.1.1.0/31    up  up  1500``
    """
    interfaces: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        for part in parts[1:]:
            if "/" in part and "." in part:
                interfaces[parts[0]] = part
                break
    return interfaces


def parse_iosxe_interfaces(output: str) -> dict[str, str]:
    """Parse Cisco IOS-XE 'show ip interface brief' — bare IPs without mask.

    Example line: ``GigabitEthernet1  172.16.0.0  YES manual up  up``

    Since IOS-XE brief output does not include prefix length, we store
    the bare IP. compare_interfaces will handle the mask-stripped comparison.
    """
    ip_re = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
    interfaces: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        iface_name = parts[0]
        # Skip header line
        if iface_name == "Interface":
            continue
        # Second field is the IP (or "unassigned")
        if ip_re.match(parts[1]):
            interfaces[iface_name] = parts[1]
    return interfaces


def parse_fortios_interfaces(output: str) -> dict[str, str]:
    """Parse FortiOS 'get system interface physical' output.

    Format::

        ==[port1]
            mode: static
            ip: 10.99.0.1 255.255.255.254

    Returns IPs in CIDR notation (e.g. ``10.99.0.1/31``).
    """
    interfaces: dict[str, str] = {}
    current_iface = ""
    for line in output.splitlines():
        line = line.strip()
        # Match interface header: ==[portN]
        iface_match = re.match(r"==\[(.+)]", line)
        if iface_match:
            current_iface = iface_match.group(1)
            continue
        # Match ip line: ip: X.X.X.X Y.Y.Y.Y
        if current_iface and line.startswith("ip:"):
            ip_parts = line.split()
            if len(ip_parts) >= 3:
                ip_addr = ip_parts[1]
                mask = ip_parts[2]
                # Skip 0.0.0.0 (unassigned)
                if ip_addr != "0.0.0.0":
                    prefix = _mask_to_prefix(mask)
                    interfaces[current_iface] = f"{ip_addr}/{prefix}"
    return interfaces


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
        else:
            # Normalize for comparison: if live IP is bare (no mask),
            # compare only the host portion of the expected CIDR
            expected_host = expected_ip.split("/")[0] if "/" in expected_ip else expected_ip
            live_host = live_ip.split("/")[0] if "/" in live_ip else live_ip
            if live_host != expected_host:
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
