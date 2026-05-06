"""Tests for scripts.push_configs PSK injection helper.

The rendered config carries a placeholder marker (`__AWS_VPN_PSK__`); the
PSK material is fetched from AWS Secrets Manager at push time so it never
lands on disk.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_inject_aws_psk_substitutes_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AWS_VPN_PSK_SECRET_ARN is set, the marker is replaced with the secret value."""
    from scripts.push_configs import _inject_aws_psk

    monkeypatch.setenv(
        "AWS_VPN_PSK_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:vpn/onprem-psk-AbCdEf",
    )

    fake_secret_value = "supersecretpsk123"
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": fake_secret_value}

    config_with_marker = (
        "crypto ikev2 keyring AWS_VPN_KR_1\n"
        " peer 203.0.113.10\n"
        "  address 203.0.113.10\n"
        "  pre-shared-key __AWS_VPN_PSK__\n"
        "!\n"
    )

    with patch("boto3.client", return_value=mock_client):
        result = _inject_aws_psk(config_with_marker)

    assert "__AWS_VPN_PSK__" not in result
    assert fake_secret_value in result


def test_inject_aws_psk_raises_on_missing_secret_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the marker is present but AWS_VPN_PSK_SECRET_ARN is unset, raise RuntimeError."""
    from scripts.push_configs import _inject_aws_psk

    monkeypatch.delenv("AWS_VPN_PSK_SECRET_ARN", raising=False)

    config_with_marker = "pre-shared-key __AWS_VPN_PSK__\n"

    with pytest.raises(RuntimeError, match="AWS_VPN_PSK_SECRET_ARN"):
        _inject_aws_psk(config_with_marker)


def test_inject_aws_psk_no_marker_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the config carries no marker, return it unchanged and never touch boto3."""
    from scripts.push_configs import _inject_aws_psk

    monkeypatch.delenv("AWS_VPN_PSK_SECRET_ARN", raising=False)

    config = "router bgp 65100\n bgp log-neighbor-changes\n"

    # boto3 must not be imported/called for non-IPsec configs
    with patch("boto3.client") as mock_client_factory:
        result = _inject_aws_psk(config)

    assert result == config
    mock_client_factory.assert_not_called()


def test_inject_aws_psk_access_denied_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A botocore AccessDenied surfaces a unique, operator-actionable message."""
    from botocore.exceptions import ClientError

    from scripts.push_configs import _inject_aws_psk

    monkeypatch.setenv("AWS_VPN_PSK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:x")

    error_response = {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}
    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = ClientError(error_response, "GetSecretValue")

    with (
        patch("boto3.client", return_value=mock_client),
        pytest.raises(RuntimeError, match="IAM principal lacks"),
    ):
        _inject_aws_psk("pre-shared-key __AWS_VPN_PSK__\n")


def test_inject_aws_psk_resource_not_found_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A botocore ResourceNotFound surfaces a unique, operator-actionable message."""
    from botocore.exceptions import ClientError

    from scripts.push_configs import _inject_aws_psk

    monkeypatch.setenv("AWS_VPN_PSK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:x")

    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}}
    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = ClientError(error_response, "GetSecretValue")

    with (
        patch("boto3.client", return_value=mock_client),
        pytest.raises(RuntimeError, match="secret ARN does not exist"),
    ):
        _inject_aws_psk("pre-shared-key __AWS_VPN_PSK__\n")


def test_inject_aws_psk_no_credentials_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A botocore NoCredentialsError surfaces a unique message."""
    from botocore.exceptions import NoCredentialsError

    from scripts.push_configs import _inject_aws_psk

    monkeypatch.setenv("AWS_VPN_PSK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:x")

    mock_client = MagicMock()
    mock_client.get_secret_value.side_effect = NoCredentialsError()

    with (
        patch("boto3.client", return_value=mock_client),
        pytest.raises(RuntimeError, match="AWS credentials not available"),
    ):
        _inject_aws_psk("pre-shared-key __AWS_VPN_PSK__\n")
