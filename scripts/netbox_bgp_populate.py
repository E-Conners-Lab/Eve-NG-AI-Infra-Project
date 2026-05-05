"""Populate netbox-bgp plugin with BGP sessions derived from the lab spec.

Creates BGP sessions for DC underlay (eBGP), DC overlay (iBGP EVPN with spines
as RR), DR collapsed (eBGP + iBGP EVPN), CE↔PE WAN, and PE iBGP. Idempotent.

Usage:
    python -m scripts.netbox_bgp_populate
"""

from __future__ import annotations

from pathlib import Path

import pynetbox
import requests
import yaml

from scripts.credentials import require_credentials

SPEC_PATH = Path(__file__).parent.parent / "specs" / "generated" / "lab_spec.yaml"


def get_or_create_asn(nb, asn_num: int, description: str = "") -> object:
    existing = nb.ipam.asns.get(asn=asn_num)
    if existing:
        return existing
    rir = nb.ipam.rirs.get(slug="private") or nb.ipam.rirs.get(slug="rfc-1918")
    return nb.ipam.asns.create(asn=asn_num, rir=rir.id, description=description)


def find_iface_ip(nb, device_name: str, ip_addr: str) -> int | None:
    """Find the NetBox IP id for `<ip_addr>/<mask>` assigned to <device_name>."""
    for ip in nb.ipam.ip_addresses.filter(device=device_name):
        if ip.address.split("/")[0] == ip_addr:
            return ip.id
    return None


def get_loopback_ip_id(nb, device_name: str) -> int | None:
    for ip in nb.ipam.ip_addresses.filter(device=device_name):
        # Loopback IPs typically have /32 mask
        if ip.address.endswith("/32"):
            return ip.id
    return None


def post_bgp_session(base_url: str, headers: dict, payload: dict) -> dict:
    r = requests.post(f"{base_url}/api/plugins/bgp/session/", json=payload, headers=headers, timeout=15)
    if r.status_code in (200, 201):
        return r.json()
    if r.status_code == 400 and "already exists" in r.text.lower():
        return {"_skip": "exists"}
    r.raise_for_status()
    return r.json()


def session_exists(base_url: str, headers: dict, local_ip_id: int, remote_ip_id: int) -> bool:
    """Quick check by local+remote IP IDs."""
    r = requests.get(
        f"{base_url}/api/plugins/bgp/session/",
        params={"local_address_id": local_ip_id, "remote_address_id": remote_ip_id},
        headers=headers,
        timeout=15,
    )
    return r.json().get("count", 0) > 0


def main() -> None:
    spec = yaml.safe_load(SPEC_PATH.read_text())
    creds = require_credentials("netbox_url", "netbox_token")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)
    base_url = creds.netbox_url.rstrip("/")
    headers = {"Authorization": f"Token {creds.netbox_token}"}

    # 1. Ensure all lab ASNs exist
    asn_descs = {
        65000: "DC-East spine + EVPN RR cluster",
        65001: "dc-leaf-1",
        65002: "dc-leaf-2",
        65003: "dc-border-1",
        65004: "dc-border-2",
        65100: "dc-ce-1 customer AS",
        65120: "br-ce-1 customer AS",
        65130: "dr-ce-1 customer AS",
        65201: "dr-leaf-1",
        65202: "dr-leaf-2",
        64500: "Service provider transport AS",
    }
    asn_objs = {}
    for asn_num, desc in asn_descs.items():
        asn_objs[asn_num] = get_or_create_asn(nb, asn_num, desc)
    print(f"ASNs ready: {sorted(asn_objs.keys())}")

    # 2. Build a fast device→ASN lookup from spec
    device_asn = {}
    for site in spec.get("sites", {}).values():
        for d in site.get("devices", []):
            if "asn" in d:
                device_asn[d["name"]] = d["asn"]
    for d in spec.get("wan_transport", {}).get("devices", []):
        if "asn" in d:
            device_asn[d["name"]] = d["asn"]

    # 3. Build BGP session list from spec (underlay eBGP via P2P link IPs)
    sessions: list[dict] = []  # list of (local_dev, local_ip, remote_dev, remote_ip, name, description)

    # Walk every interface in the spec; for each one with peer + ipv4, the peer's
    # corresponding interface gives us the remote IP. Avoid duplicates (A↔B once).
    seen = set()

    def _add_underlay(local_dev, local_ip, remote_dev, remote_ip, label):
        key = tuple(sorted([(local_dev, local_ip), (remote_dev, remote_ip)]))
        if key in seen:
            return
        seen.add(key)
        sessions.append({
            "local_dev": local_dev, "local_ip": local_ip,
            "remote_dev": remote_dev, "remote_ip": remote_ip,
            "name": label, "kind": "underlay",
        })

    def _walk_devices(devs, links_section_name: str):
        for d in devs:
            for iface in d.get("interfaces", []):
                if "peer" not in iface or "ipv4" not in iface:
                    continue
                if iface.get("ha_role") == "heartbeat":
                    continue  # not a BGP session
                local_ip = iface["ipv4"].split("/")[0]
                remote_dev = iface["peer"]
                remote_iface_name = iface.get("peer_interface")
                # Find peer's IP on that interface
                peer_dev = next((x for x in all_devs if x["name"] == remote_dev), None)
                if not peer_dev:
                    continue
                peer_iface = next((i for i in peer_dev.get("interfaces", []) if i["name"] == remote_iface_name), None)
                if not peer_iface or "ipv4" not in peer_iface:
                    continue
                remote_ip = peer_iface["ipv4"].split("/")[0]
                # Only build sessions where both ends have an ASN AND they peer L3.
                # Skip CE↔FW and FW↔border (transit, no BGP per spec).
                if d["name"] not in device_asn or remote_dev not in device_asn:
                    continue
                # Skip FW-as-endpoint (FortiGate not running BGP in this lab).
                if d.get("role") == "firewall" or peer_dev.get("role") == "firewall":
                    continue
                # Same logic: spine↔border, spine↔leaf, leaf↔leaf, ce↔pe — all valid BGP
                _add_underlay(d["name"], local_ip, remote_dev, remote_ip,
                              f'{d["name"]}↔{remote_dev}')

    all_devs = []
    for site in spec.get("sites", {}).values():
        all_devs.extend(site.get("devices", []))
    all_devs.extend(spec.get("wan_transport", {}).get("devices", []))
    for sec in spec.get("security", {}).values():
        all_devs.extend(sec.get("firewalls", []))

    for site in spec.get("sites", {}).values():
        _walk_devices(site.get("devices", []), "")
    _walk_devices(spec.get("wan_transport", {}).get("devices", []), "")

    # PE↔PE iBGP — peer via loopbacks, no direct interface IP match in spec.
    # Add explicitly.
    pe_pair = [d for d in spec["wan_transport"]["devices"] if d["name"].startswith("sp-pe")]
    if len(pe_pair) == 2:
        a, b = pe_pair
        a_lo = a["loopback0"].split("/")[0]
        b_lo = b["loopback0"].split("/")[0]
        sessions.append({
            "local_dev": a["name"], "local_ip": a_lo,
            "remote_dev": b["name"], "remote_ip": b_lo,
            "name": "sp-pe-1↔sp-pe-2 iBGP",
            "kind": "ibgp",
        })

    # DC EVPN overlay — spines as RRs, peer with each leaf/border via loopbacks
    dc_devs = spec["sites"]["dc_east"]["devices"]
    spines = [d for d in dc_devs if d.get("role") == "spine"]
    clients = [d for d in dc_devs if d.get("role") in ("leaf", "border-leaf")]
    for sp in spines:
        sp_lo = sp["loopback0"].split("/")[0]
        for cl in clients:
            cl_lo = cl["loopback0"].split("/")[0]
            sessions.append({
                "local_dev": sp["name"], "local_ip": sp_lo,
                "remote_dev": cl["name"], "remote_ip": cl_lo,
                "name": f'{sp["name"]}↔{cl["name"]} EVPN (RR)',
                "kind": "evpn",
            })

    # DR collapsed EVPN — leaves peer with each other via loopbacks
    dr_devs = spec["sites"]["dr_west"]["devices"]
    dr_leaves = [d for d in dr_devs if d.get("role") == "leaf"]
    if len(dr_leaves) == 2:
        a, b = dr_leaves
        sessions.append({
            "local_dev": a["name"], "local_ip": a["loopback0"].split("/")[0],
            "remote_dev": b["name"], "remote_ip": b["loopback0"].split("/")[0],
            "name": f'{a["name"]}↔{b["name"]} EVPN',
            "kind": "evpn",
        })

    # 4. Push sessions into NetBox
    print(f"\nCreating {len(sessions)} BGP sessions:")
    created = skipped = failed = 0
    for s in sessions:
        local_ip_id = find_iface_ip(nb, s["local_dev"], s["local_ip"])
        remote_ip_id = find_iface_ip(nb, s["remote_dev"], s["remote_ip"])
        if not local_ip_id or not remote_ip_id:
            print(f"  ✗ {s['name']}: missing IP in NetBox (local={local_ip_id} remote={remote_ip_id})")
            failed += 1
            continue
        if session_exists(base_url, headers, local_ip_id, remote_ip_id):
            skipped += 1
            continue
        local_dev_obj = nb.dcim.devices.get(name=s["local_dev"])
        remote_dev_obj = nb.dcim.devices.get(name=s["remote_dev"])
        local_asn = device_asn.get(s["local_dev"])
        remote_asn = device_asn.get(s["remote_dev"])
        payload = {
            "name": s["name"],
            "local_address": local_ip_id,
            "remote_address": remote_ip_id,
            "local_as": asn_objs[local_asn].id,
            "remote_as": asn_objs[remote_asn].id,
            "device": local_dev_obj.id,
            "site": local_dev_obj.site.id if local_dev_obj.site else None,
            "status": "active",
            "description": f"{s.get('kind','bgp')} session ({local_asn}↔{remote_asn})",
        }
        try:
            result = post_bgp_session(base_url, headers, payload)
            if result.get("_skip"):
                skipped += 1
            else:
                created += 1
                print(f"  ✓ {s['name']}  ({local_asn}↔{remote_asn})")
        except Exception as e:
            print(f"  ✗ {s['name']}: {e}")
            failed += 1

    print(f"\nDone — created={created}  skipped={skipped}  failed={failed}  total={len(sessions)}")


if __name__ == "__main__":
    main()
