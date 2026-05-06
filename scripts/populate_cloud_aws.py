"""Seed NetBox with the cloud-aws site and IPsec tunnel data on dc-ce-1.

Reads AWS Terraform outputs from ``.aws_outputs.json`` (produced by
``make sync-aws-outputs``) for the strongSwan EIP, PSK secret ARN, and
tunnel inner CIDRs. Idempotently creates:

- Site ``cloud-aws``
- Device role ``cloud-vpn``
- Device ``aws-vpn-1`` (linux platform, Generic manufacturer, Alpine Linux dtype)
- Prefixes for the VPC private subnets (default 10.0.64.0/20, 10.0.80.0/20)
- ``Tunnel0`` virtual interface on ``dc-ce-1``
- ``tun0`` virtual interface on ``aws-vpn-1``
- Cable connecting the two
- Idempotent ``local_context_data`` merges:
  ``observed_interfaces`` + ``vpn_tunnels`` on dc-ce-1, ``agent_boundary=excluded``
  on aws-vpn-1 — these feed the generator at the next ``make generate-spec`` run.

Run AFTER ``make sync-aws-outputs``.

Usage:
    python -m scripts.populate_cloud_aws
    python -m scripts.populate_cloud_aws --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import pynetbox

from scripts.credentials import require_credentials
from scripts.populate_netbox import _get_or_create
from scripts.populate_netbox_contexts import _update_context

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
AWS_OUTPUTS_PATH = PROJECT_ROOT / ".aws_outputs.json"

# Defaults applied if a TF output is absent and no env-var override is set.
DEFAULT_VPC_PRIVATE_PREFIXES = ["10.0.64.0/20", "10.0.80.0/20"]
DEFAULT_TUNNEL_LOCAL_INNER_AWS = "169.254.10.2/30"
DEFAULT_TUNNEL_REMOTE_INNER_AWS = "169.254.10.1/30"


def _read_aws_outputs() -> dict:
    """Read terraform output -json from ``.aws_outputs.json``; allow env-var overrides.

    ``terraform output -json`` wraps each value in ``{"sensitive", "type", "value"}``;
    we unwrap to a flat ``{key: value}`` dict. Env vars take precedence so an operator
    can iterate without re-running ``make sync-aws-outputs``.
    """
    out: dict = {}
    if AWS_OUTPUTS_PATH.exists():
        raw = json.loads(AWS_OUTPUTS_PATH.read_text())
        for key, item in raw.items():
            if isinstance(item, dict) and "value" in item:
                out[key] = item["value"]
            else:
                out[key] = item

    env_map = {
        "strongswan_eip": "AWS_STRONGSWAN_EIP",
        "psk_secret_arn": "AWS_VPN_PSK_SECRET_ARN",
        "tunnel_local_inner": "AWS_TUNNEL_LOCAL_INNER",
        "tunnel_remote_inner": "AWS_TUNNEL_REMOTE_INNER",
    }
    for tf_key, env_key in env_map.items():
        env_val = os.environ.get(env_key)
        if env_val:
            out[tf_key] = env_val

    return out


def _require(out: dict, key: str, hint: str) -> str:
    val = out.get(key)
    if not val:
        raise RuntimeError(
            f"Missing AWS terraform output '{key}'. {hint} "
            "Run `make sync-aws-outputs` (after `terraform apply`) "
            "or set the equivalent env var."
        )
    return str(val)


def populate(nb: pynetbox.api, aws_outputs: dict, dry_run: bool = False) -> None:
    """Seed cloud-aws site and IPsec tunnel data idempotently."""

    strongswan_eip = _require(aws_outputs, "strongswan_eip", "Run terraform apply first.")
    psk_secret_arn = _require(aws_outputs, "psk_secret_arn", "Check the vpn module outputs.")

    # AWS terraform outputs are from the AWS perspective:
    #   tunnel_local_inner  → AWS-side inner addr  → dc-ce-1's REMOTE inner
    #   tunnel_remote_inner → CE-side inner addr   → dc-ce-1's LOCAL inner
    # Swap when we write to NetBox so the lab spec is from the CE perspective.
    aws_inner = aws_outputs.get("tunnel_local_inner", DEFAULT_TUNNEL_LOCAL_INNER_AWS)
    ce_inner = aws_outputs.get("tunnel_remote_inner", DEFAULT_TUNNEL_REMOTE_INNER_AWS)

    vpc_prefixes_raw = aws_outputs.get("vpc_private_prefixes", DEFAULT_VPC_PRIVATE_PREFIXES)
    if isinstance(vpc_prefixes_raw, str):
        vpc_prefixes = [p.strip() for p in vpc_prefixes_raw.split(",") if p.strip()]
    else:
        vpc_prefixes = list(vpc_prefixes_raw)

    print(f"AWS strongSwan EIP:    {strongswan_eip}")
    print(f"PSK secret ARN:        {psk_secret_arn}")
    print(f"VPC private prefixes:  {vpc_prefixes}")
    print(f"CE-side tunnel inner:  {ce_inner}")
    print(f"AWS-side tunnel inner: {aws_inner}")

    if dry_run:
        print("\nDry run — no NetBox changes.")
        return

    print("\nStep 1: Site cloud-aws")
    site = _get_or_create(
        nb.dcim.sites,
        {"slug": "cloud-aws"},
        {"name": "Cloud-AWS", "slug": "cloud-aws", "status": "active"},
        "site",
    )

    print("Step 2: Manufacturer / platform / device type")
    mfr = _get_or_create(
        nb.dcim.manufacturers,
        {"slug": "generic"},
        {"name": "Generic", "slug": "generic"},
        "manufacturer",
    )
    platform = _get_or_create(
        nb.dcim.platforms,
        {"slug": "linux"},
        {"name": "Linux", "slug": "linux", "manufacturer": mfr.id},
        "platform",
    )
    dtype = _get_or_create(
        nb.dcim.device_types,
        {"slug": "alpine-linux"},
        {"model": "Alpine Linux", "slug": "alpine-linux", "manufacturer": mfr.id},
        "device type",
    )

    print("Step 3: Role cloud-vpn")
    role = _get_or_create(
        nb.dcim.device_roles,
        {"slug": "cloud-vpn"},
        {"name": "Cloud VPN", "slug": "cloud-vpn", "color": "00bcd4"},
        "role",
    )

    print("Step 4: Device aws-vpn-1")
    aws_vpn_1 = nb.dcim.devices.get(name="aws-vpn-1")
    if not aws_vpn_1:
        aws_vpn_1 = nb.dcim.devices.create(
            {
                "name": "aws-vpn-1",
                "device_type": dtype.id,
                "role": role.id,
                "site": site.id,
                "platform": platform.id,
                "status": "active",
            }
        )
        print("  Created device: aws-vpn-1")

    print("Step 5: aws-vpn-1:tun0")
    tun0_aws = nb.dcim.interfaces.get(device="aws-vpn-1", name="tun0")
    if not tun0_aws:
        tun0_aws = nb.dcim.interfaces.create(
            {
                "device": aws_vpn_1.id,
                "name": "tun0",
                "type": "virtual",
                "description": "IPsec tunnel to dc-ce-1",
            }
        )
        print("  Created interface aws-vpn-1:tun0")

    print("Step 6: dc-ce-1:Tunnel0")
    dc_ce_1 = nb.dcim.devices.get(name="dc-ce-1")
    if not dc_ce_1:
        raise RuntimeError(
            "dc-ce-1 not found in NetBox. Run `python -m scripts.populate_netbox` first."
        )
    tunnel0_ce = nb.dcim.interfaces.get(device="dc-ce-1", name="Tunnel0")
    if not tunnel0_ce:
        tunnel0_ce = nb.dcim.interfaces.create(
            {
                "device": dc_ce_1.id,
                "name": "Tunnel0",
                "type": "virtual",
                "description": "to-aws-strongswan",
            }
        )
        print("  Created interface dc-ce-1:Tunnel0")

    print("Step 7: Cable dc-ce-1:Tunnel0 <-> aws-vpn-1:tun0")
    existing_cables = list(nb.dcim.cables.filter(device="dc-ce-1"))
    already_cabled = False
    for c in existing_cables:
        a_names = {t.name for t in c.a_terminations}
        b_names = {t.name for t in c.b_terminations}
        if "Tunnel0" in (a_names | b_names) and "tun0" in (a_names | b_names):
            already_cabled = True
            break
    if not already_cabled:
        nb.dcim.cables.create(
            {
                "a_terminations": [{"object_type": "dcim.interface", "object_id": tunnel0_ce.id}],
                "b_terminations": [{"object_type": "dcim.interface", "object_id": tun0_aws.id}],
                "status": "connected",
            }
        )
        print("  Created cable")

    print("Step 8: VPC private prefixes")
    for prefix_cidr in vpc_prefixes:
        _get_or_create(
            nb.ipam.prefixes,
            {"prefix": prefix_cidr},
            {
                "prefix": prefix_cidr,
                "site": site.id,
                "status": "active",
                "description": "AWS VPC private subnet (cloud-aws)",
            },
            "prefix",
        )

    print("Step 9: dc-ce-1 config_context (observed_interfaces, vpn_tunnels)")
    ctx_patch = {
        "observed_interfaces": ["Tunnel0"],
        "vpn_tunnels": [
            {
                "name": "aws-tunnel-1",
                "tunnel_type": "ipsec",
                "local_device": "dc-ce-1",
                "local_interface": "Tunnel0",
                "local_inner_ip": ce_inner,
                "remote_endpoint": strongswan_eip,
                "remote_inner_ip": aws_inner,
                "psk_secret_ref": psk_secret_arn,
                "routed_prefixes": list(vpc_prefixes),
                "tunnel_source": "GigabitEthernet1",
                "ike_version": 2,
                "dh_group": 14,
                "encryption": "aes-cbc-256",
                "integrity": "sha256",
            }
        ],
    }
    _update_context(nb, "dc-ce-1", ctx_patch)
    print("  Patched dc-ce-1 local_context_data")

    print("Step 10: aws-vpn-1 config_context (agent_boundary)")
    _update_context(nb, "aws-vpn-1", {"agent_boundary": "excluded"})
    print("  Patched aws-vpn-1 local_context_data")

    print("\ncloud-aws seed complete.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed NetBox with the cloud-aws site + IPsec tunnel data on dc-ce-1"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    args = parser.parse_args()

    aws_outputs = _read_aws_outputs()

    if not aws_outputs and not args.dry_run:
        print(
            f"ERROR: No AWS outputs at {AWS_OUTPUTS_PATH} and no env-var overrides set.\n"
            "Run `make sync-aws-outputs` (after `terraform apply`) first.",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = require_credentials("netbox_url", "netbox_token")
    nb = pynetbox.api(creds.netbox_url, token=creds.netbox_token)

    try:
        populate(nb, aws_outputs, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
