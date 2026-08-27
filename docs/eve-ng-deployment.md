# EVE-NG Deployment Guide

This document captures every lesson learned deploying the 21-node AI Infrastructure Lab to EVE-NG. It exists to prevent repeating mistakes.

## Prerequisites

- EVE-NG Pro running on Proxmox (Dell PowerEdge R640)
- EVE-NG management IP: 192.168.68.240
- Minimum 64GB RAM allocated to EVE-NG VM (128GB recommended)
- CPU type set to `host` in Proxmox (required for nested KVM)

## Images Required

| Image | Folder Name | Min RAM | Min CPU | Notes |
|-------|------------|---------|---------|-------|
| Arista vEOS 4.33.1.1F | `veos-4.33.1.1F` | 2048 | 1 | |
| Cisco C8000v 17.13 | `c8000v-17.13.01a` | 8192 | **4** | Crashes with fewer than 4 CPUs |
| FortiGate VM | `fortinet-FGT` | 2048 | 1 | |
| Alpine Linux | `linux-alpine` | 512 | 1 | Must install from ISO (see below) |

## Critical API Parameters

### config parameter — MUST be "0"

When creating nodes via the EVE-NG API, the `config` field must be set to `"0"`.

- `"0"` = no startup config, just boot (this is what the UI sets)
- `"none"` = maps to `"Unconfigured"` in EVE-NG XML
- `"Unconfigured"` causes **instant boot failure** — QEMU starts, creates TAP interfaces, then dies within 1 second with no useful error message

This was the root cause of C8000v nodes dying on startup. The syslog shows QEMU launching, dmesg shows TAP interfaces created then destroyed, but no error is logged. Only comparing API-created vs UI-created node XML revealed the difference.

### C8000v requires 4 vCPUs

The Cisco Catalyst 8000V requires a minimum of 4 vCPUs. With `cpu=1`, the IOS-XE kernel panics immediately on startup. The EVE-NG UI template defaults to `cpu=4`, but our API call set `cpu=1`. No error is logged — the node simply starts and stops.

### Interface naming per vendor

EVE-NG uses short interface labels that differ from IOS/EOS naming:

| Vendor | Spec Name | EVE-NG Label | Index |
|--------|-----------|-------------|-------|
| Arista vEOS | Management1 | Mgmt1 | 0 |
| Arista vEOS | Ethernet1 | Eth1 | 1 |
| Cisco C8000v | GigabitEthernet1 | Gi1 | 0 |
| FortiGate | port1 | port1 | 0 |
| Alpine Linux | eth0 | **e0** | 0 |
| Alpine Linux | eth1 | **e1** | 1 |

Alpine Linux labels are the most surprising — `e0`/`e1` not `eth0`/`eth1`.

## EVE-NG Pro Path Handling

EVE-NG Pro's `evengsdk` library prepends `/admin/` to lab paths unless the path starts with `/`. Labs on this server live at root `/`, so always pass paths with a leading slash:

```python
# Correct — resolves to /AI-Infra-Lab.unl
eve.add_node("/AI-Infra-Lab", ...)

# Wrong — resolves to /admin/AI-Infra-Lab.unl (404)
eve.add_node("AI-Infra-Lab", ...)
```

## Lab Locking

EVE-NG holds a file lock during lab saves. Rapid successive API calls cause 400 or 412 errors:

- **Don't** mix custom HTTP clients and `evengsdk` in the same session
- **Do** use `evengsdk`'s `connect_node_to_node()` for wiring — it handles locking
- **Do** use `evengsdk`'s `connect_node_to_cloud()` for management connections
- Only one lab can be open at a time in EVE-NG Pro

## Alpine Linux Image Installation

The cloud-init Alpine images don't work in EVE-NG. Install from ISO via QEMU directly on the EVE-NG server:

```bash
cd /opt/unetlab/addons/qemu/linux-alpine/
wget https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-virt-3.21.0-x86_64.iso -O cdrom.iso
qemu-img create -f qcow2 virtioa.qcow2 2G

# Boot and install directly (not through EVE-NG)
qemu-system-x86_64 \
  -m 512 \
  -drive file=virtioa.qcow2,if=virtio,format=qcow2 \
  -cdrom cdrom.iso \
  -boot d \
  -nographic \
  -enable-kvm
```

Inside the Alpine live environment:
1. Login as `root` (no password)
2. Run `setup-alpine`
3. Disk: **`vda`** (not `sda` — EVE-NG uses virtio)
4. Mode: **`sys`**
5. After install: `poweroff`

Then clean up:
```bash
rm cdrom.iso
/opt/unetlab/wrappers/unl_wrapper -a fixpermissions
```

This produces one shared golden template (`/opt/unetlab/addons/qemu/linux-alpine/virtioa.qcow2`) that every Alpine node instance in the lab boots from via a per-node copy-on-write overlay. Do this once — it is not per-node.

### Resetting the golden image's root password

`setup-alpine` sets a real root password during install. If it's later forgotten (e.g. a fresh session inherits a lab someone else built), reset it **offline, without booting the image**, using `virt-customize` on the EVE-NG host itself (`libguestfs-tools` — `virt-customize`, `guestfish`, `qemu-nbd` are all pre-installed on this appliance):

```bash
# Never pass the password as a literal CLI argument — it echoes into shell
# history and into any terminal/log capturing the session. Write it to a
# root-only file first (scp it in, or `cat > file` with history disabled),
# then reference the file:
virt-customize -a /opt/unetlab/addons/qemu/linux-alpine/virtioa.qcow2 \
  --root-password file:/root/.tmp_pw
shred -u /root/.tmp_pw
```

Only edit the golden image while **no lab node is currently running from it** — QEMU instances hold it open as a read-only COW backing file, and editing it live risks inconsistency.

### Console password echo (telnet/nc)

macOS no longer ships a `telnet` client; `nc <eve-ng-host> <console-port>` works as a substitute for EVE-NG's raw serial console, but unlike a real telnet client it does no IAC/echo negotiation. Practical effect: **Alpine's `login` prompt echoes the password back in plaintext** over this raw channel (unlike a normal SSH session, where the password prompt is masked). This isn't fixable client-side — it's inherent to using a raw socket against this console type — so treat any credential typed at this prompt as exposed to whatever is capturing the session, and rotate accordingly rather than relying on the console to hide it.

## Node Stop/Start — the `stopmode` parameter is undocumented

`GET /api/labs/{lab}/nodes/{id}/stop` alone returns `400 {"message":"Request not valid (60027)."}` on this EVE-NG version (6.5.0-21-PRO) — it is **not** the real endpoint. The actual API, confirmed by reading the web UI's own minified JS bundle (`/opt/unetlab/html/assets/_plugin-vue_export-helper-*.js`; `api.php` itself is ionCube-encoded and unreadable, as is `unl_wrapper`) is:

```
GET /api/labs/{lab}/nodes/{id}/stop/stopmode={0|1}
```

- **`stopmode=0`** — graceful (ACPI powerdown signal to the guest). Works for Alpine (runs `acpid`). **Hangs indefinitely** for Arista veos and Cisco c8000v images — they don't implement ACPI shutdown handling, so the API call never returns and the node gets stuck in an intermediate status (`status=4`, "stopping") until force-stopped.
- **`stopmode=1`** — force (hard kill of the QEMU process). Works universally. Use this for network-OS nodes, and run `write memory` / `copy running-config startup-config` first since it doesn't give the guest a chance to save anything itself.

Also: **a node's console port (the `url` field, e.g. `telnet://host:PORT`) changes on every restart.** Re-fetch `GET /nodes/{id}` after each start rather than reusing a cached port.

## Interface Rewiring Requires a Node Restart

Calling `PUT /nodes/{id}/interfaces` to attach a new network to an **already-running** node's interface updates the lab's saved topology (and reports success), but does **not** hot-plug the change into the live QEMU process — a running node's virtual NIC backend is fixed at boot time. Symptom: the switch/router side shows the port as `connected`/up at L1 with correct VLAN membership, but `0 packets input` forever, and ARP requests from the new node never get a reply.

Fix: stop and start the node (see stopmode above) so it boots with the current interface map. If it's a network-OS node with live routing/EVPN state, `write memory` first — the restart is otherwise safe and state reconverges automatically.

This only matters when wiring into nodes that were **already running** before the API call. A node created and wired while stopped, then started fresh, doesn't need this.

## Creating Bridge Networks via the API

`POST /api/labs/{lab}/networks` with a sparse payload (`type`, `name`, `left`, `top` only) returns `201` with a plausible-looking `{"id": N}` — but the network is **not actually persisted**; an immediate `GET /networks/{N}` 404s, indefinitely. The fix is to send the full field set the UI itself sends (`count`, `smart`, `native_vlan`, `style`, `icon`, `linkstyle`, `color`, `label`, `visibility`, `hideme`, `pnet_out`), not just the ones that seem semantically relevant:

```python
payload = {
    "type": "bridge", "name": name, "left": left, "top": top,
    "count": 0, "smart": 0, "native_vlan": 1,
    "style": "Solid", "icon": "lan.png", "linkstyle": "Straight",
    "color": "", "label": "", "visibility": 1, "hideme": 0, "pnet_out": ""
}
```

Always verify persistence with a follow-up `GET /networks/{id}` before wiring anything to the returned id — don't trust the `201` alone.

Also note: newly created nodes default to `icon: "Router.png"` regardless of `template`/`image` — cosmetic only (the actual boot behavior is driven by `template`/`image`), but worth a follow-up `PUT /nodes/{id}` with `{"icon": "Linux.png"}` (or whatever fits) if the canvas display matters. Valid icon filenames live in `/opt/unetlab/html/images/icons/` on the EVE-NG host.

## Management Network

All devices connect to Cloud0 (pnet0) for out-of-band management:

| Device | Management IP | Mgmt Interface |
|--------|--------------|----------------|
| dc-spine-1 | 192.168.68.110 | Mgmt1 |
| dc-spine-2 | 192.168.68.111 | Mgmt1 |
| dc-leaf-1 | 192.168.68.112 | Mgmt1 |
| dc-leaf-2 | 192.168.68.113 | Mgmt1 |
| dc-border-1 | 192.168.68.114 | Mgmt1 |
| dc-border-2 | 192.168.68.115 | Mgmt1 |
| dc-host-1 | 192.168.68.116 | e1 |
| dc-host-2 *(not provisioned)* | 192.168.68.117 | e1 |
| dc-fw-1 *(removed — see CLAUDE.md)* | 192.168.68.118 | port6 |
| dc-fw-2 *(removed — see CLAUDE.md)* | 192.168.68.119 | port6 |
| dc-ce-1 | 192.168.68.120 | Gi5 |
| sp-pe-1 | 192.168.68.121 | Gi5 |
| sp-pe-2 | 192.168.68.122 | Gi5 |
| br-ce-1 | 192.168.68.123 | Gi5 |
| br-host-1 | 192.168.68.124 | e1 |
| dr-leaf-1 | 192.168.68.125 | Mgmt1 |
| dr-leaf-2 | 192.168.68.126 | Mgmt1 |
| dr-fw-1 *(removed — see CLAUDE.md)* | 192.168.68.127 | port6 |
| dr-fw-2 *(removed — see CLAUDE.md)* | 192.168.68.128 | port6 |
| dr-ce-1 | 192.168.68.129 | Gi5 |
| dr-host-1 | 192.168.68.130 | e1 |

Only `dc-host-1`, `br-host-1`, and `dr-host-1` are currently provisioned as the lab's Alpine test hosts (as of 2026-08-27) — see the next section for their full data-plane setup. `dc-host-2` was never built. The firewall rows predate the FortiGate removal documented in `CLAUDE.md` and are listed here only because their mgmt IPs are still reserved.

## Host Node Data-Plane Onboarding

Each Alpine host is dual-homed: `eth0` (EVE-NG label `e0`) carries site data traffic, `eth1` (`e1`) carries out-of-band management, connected to Cloud0 per the table above. `/etc/network/interfaces`:

```
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet static
    address <site-ip>
    netmask 255.255.255.0
    gateway <site-anycast-or-router-gateway>

auto eth1
iface eth1 inet static
    address <mgmt-ip-from-table-above>
    netmask 255.255.252.0
```

**The default route (`gateway`) belongs on `eth0`, not `eth1`.** Putting it on `eth1` (as if the OOB mgmt path were the "real" default) breaks all cross-site traffic silently: the host can still reach its own local gateway and the mgmt LAN fine, but any packet destined for another site's subnet gets sent out the mgmt interface toward `192.168.68.1` instead of into the fabric, and is dropped with no error on the host side. `192.168.68.0/22` doesn't need to be the default route — it's already directly connected via `eth1`'s own subnet route.

Current host wiring:

| Host | Site IP / gateway | Uplink (data, `e0`) | Notes |
|------|-------------------|----------------------|-------|
| dc-host-1 | 10.10.1.10/24, gw 10.10.1.1 | `dc-leaf-1 Eth3` (VLAN 100 `SERVERS_A`) | fresh bridge network |
| br-host-1 | 10.20.1.10/24, gw 10.20.1.1 | `br-ce-1 Gi3` (already had 10.20.1.1/24 configured) | fresh bridge network |
| dr-host-1 | 10.30.1.10/24, gw 10.30.1.1 | `dr-leaf-1 Eth3` (VLAN 100 `SERVERS_DR`) | reused an orphaned bridge network left over from before these hosts were deleted and rebuilt — check `count`/connected-interfaces on any "unused-looking" network before creating a new one instead of reusing it |

Before wiring a new host's uplink, verify the target switch/router port's VLAN or IP config with a read-only check (`show vlan brief`, `show ip interface brief`) rather than assuming the first free port is correct — port numbering and VLAN assignment don't always line up, especially after ports have been repurposed during earlier lab rebuilds.

## Debugging Node Startup Failures

When a node starts and immediately stops:

1. **Check syslog for QEMU command:** `grep "cmd is" /var/log/syslog | tail -5`
2. **Check dmesg for TAP interface lifecycle:** `dmesg | tail -30`
3. **Compare XML with a working node:** Parse both lab `.unl` files and diff the `<node>` attributes
4. **Check instance overlay:** `qemu-img info /opt/unetlab/tmp/0/{lab-uuid}/{node-id}/virtioa.qcow2`
5. **Wipe and retry:** Right-click node > Wipe (clears instance overlay)
6. **Key attributes to compare:** `config`, `cpu`, `ram`, `ethernet`, `cpulimit`

The most common silent killer is `config="Unconfigured"` — it produces zero useful error output.

## Topology Creation Command

```bash
python -m scripts.create_topology              # deploy to EVE-NG
python -m scripts.create_topology --dry-run     # preview without deploying
```

Requires `.env` with `EVE_NG_HOST` and `EVE_NG_PASSWORD` set.
