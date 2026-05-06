# cloud_tunnel_health

## Purpose

Validates IPsec tunnel state from a CE router (e.g. `dc-ce-1`) toward a cloud
strongSwan endpoint (e.g. the AWS `vpn/onprem-psk` peer in `cloud_aws`). Surfaces
IKE phase 1 state, IPsec phase 2 (ESP) state, peer endpoint, packet counters,
encryption parameters, and SA lifetime.

## Commands used

- `show crypto ikev2 sa detail` — IKEv2 SA table with status, encryption, DH group,
  lifetime/active time
- `show crypto ipsec sa` — IPsec packet counters and inbound/outbound ESP SA blocks

## Why a hand-rolled parser

ntc-templates ships TextFSM templates for many `show` commands but does NOT cover
`show crypto ikev2 sa detail` or `show crypto ipsec sa` for the `cisco_ios`/`cisco_xe`
families (verified at the version pinned in this repo). The hand-rolled parser in
`skill.py` is unit-tested against golden samples under `tests/unit/test_mcp_server.py`.

## Pass criteria

- `ike_state == "READY"` (IKEv2 phase 1 is up)
- `esp_state == "INSTALLED"` (both inbound and outbound ESP SAs are up)
- `encrypted_packets > 0` and `decrypted_packets > 0` over a sampling window
  (zero counters can also mean "tunnel up but no traffic yet")

## Defensive return contract

The MCP tool **never** returns `None` for `ike_state` or `esp_state`. If the
parser returns an empty list (e.g. SSH command returned empty output, garbled
text, or the command was rejected), the tool returns `"UNKNOWN"` for both
fields. This prevents a false-green where the agent reports a tunnel as fine
when in fact it just couldn't read the state.

## Alert trigger

A `cloud_tunnel_health` call returning `ike_state != "READY"` for two consecutive
samples should alert. The Lambda `vpn-monitor` (cloud-devops-pipeline) does the
same check from the AWS side via `swanctl --list-sas`; both signals should agree.
