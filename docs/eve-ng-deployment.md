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
| dc-host-2 | 192.168.68.117 | e1 |
| dc-fw-1 | 192.168.68.118 | port6 |
| dc-fw-2 | 192.168.68.119 | port6 |
| dc-ce-1 | 192.168.68.120 | Gi5 |
| sp-pe-1 | 192.168.68.121 | Gi5 |
| sp-pe-2 | 192.168.68.122 | Gi5 |
| br-ce-1 | 192.168.68.123 | Gi5 |
| br-host-1 | 192.168.68.124 | e1 |
| dr-leaf-1 | 192.168.68.125 | Mgmt1 |
| dr-leaf-2 | 192.168.68.126 | Mgmt1 |
| dr-fw-1 | 192.168.68.127 | port6 |
| dr-fw-2 | 192.168.68.128 | port6 |
| dr-ce-1 | 192.168.68.129 | Gi5 |
| dr-host-1 | 192.168.68.130 | e1 |

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
