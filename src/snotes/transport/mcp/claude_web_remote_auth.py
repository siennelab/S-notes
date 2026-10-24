"""Fail-closed Auth0 OAuth proxy for Claude Web's S-notes MCP endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import pwd
from typing import Any

from fastmcp.server.auth.oidc_proxy import OIDCConfiguration

from src.snotes.transport.mcp.greencloud_remote_auth import (
    MAX_CONFIG_BYTES,
    MAX_CREDENTIAL_BYTES,
    GreenCloudRemoteAuthError,
    RestrictedAuth0Provider,
    _load_json_file,
    _require_exact_keys,
    _required_string,
    _required_string_list,
    _validated_https_url,
    build_scoped_auth0_provider,
)


ClaudeWebRemoteAuthError = GreenCloudRemoteAuthError

NOTES_ACCESS_SCOPE = "notes"
CONFIG_PATH = Path("/srv/snotes/claude-web-remote/config.json")
CREDENTIAL_NAME = "claude-web-oauth.json"
RUNTIME_ACCOUNT = "claude"
RUNTIME_UID = 2001
PROXY_BASE_URL = "https://snotes-claude.ke-den.de"
AUDIENCE = f"{PROXY_BASE_URL}/mcp"
OAUTH_STORAGE_DIRECTORY = Path("/srv/snotes/claude-web-remote/oauth")
CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
LOOPBACK_REDIRECT_PATTERN = "http://127.0.0.1:*"
ALLOWED_REDIRECT_URIS = frozenset(
    {CLAUDE_REDIRECT_URI, LOOPBACK_REDIRECT_PATTERN}
)

_CONFIG_KEYS = frozenset(
    {
        "issuer_url",
        "jwks_url",
        "auth0_config_url",
        "proxy_base_url",
        "audience",
        "oauth_storage_directory",
        "allowed_client_redirect_uris",
    }
)
_CREDENTIAL_KEYS = frozenset(
    {
        "auth0_client_id",
        "auth0_client_secret",
        "allowed_subjects",
        "storage_encryption_key",
        "jwt_signing_key",
    }
)


@dataclass(frozen=True)
class ClaudeWebRemoteAuthConfig:
    issuer_url: str
    jwks_url: str
    auth0_config_url: str
    auth0_client_id: str
    auth0_client_secret: str
    proxy_base_url: str
    audience: str
    oauth_storage_directory: Path
    allowed_client_redirect_uris: tuple[str, ...]
    allowed_subjects: frozenset[str]
    storage_encryption_key: str
    jwt_signing_key: str


def build_auth_provider() -> RestrictedAuth0Provider:
    """Load Claude Web's fixed files as the dedicated runtime account."""
    try:
        runtime_uid = pwd.getpwnam(RUNTIME_ACCOUNT).pw_uid
    except KeyError as error:
        raise ClaudeWebRemoteAuthError("runtime_account_missing") from error
    if runtime_uid != RUNTIME_UID:
        raise ClaudeWebRemoteAuthError("runtime_account_uid_mismatch")
    if os.geteuid() != runtime_uid:
        raise ClaudeWebRemoteAuthError("runtime_account_mismatch")
    config = load_auth_config(expected_owner_uid=runtime_uid)
    return build_auth0_provider(
        config=config,
        expected_owner_uid=runtime_uid,
    )


def load_auth_config(*, expected_owner_uid: int) -> ClaudeWebRemoteAuthConfig:
    """Load root-owned public config and Claude's systemd credential."""
    config_document = _load_json_file(
        CONFIG_PATH,
        maximum_bytes=MAX_CONFIG_BYTES,
        error_prefix="config",
        allowed_modes={0o600, 0o644},
        allowed_owner_uids={0},
    )
    _require_exact_keys(config_document, _CONFIG_KEYS, "config_keys_invalid")

    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if not credentials_directory:
        raise ClaudeWebRemoteAuthError("credentials_directory_missing")
    directory = Path(credentials_directory)
    if not directory.is_absolute():
        raise ClaudeWebRemoteAuthError("credentials_directory_not_absolute")
    credential_document = _load_json_file(
        directory / CREDENTIAL_NAME,
        maximum_bytes=MAX_CREDENTIAL_BYTES,
        error_prefix="credential",
        allowed_modes={0o400, 0o440, 0o600},
        allowed_owner_uids={0, expected_owner_uid},
    )
    _require_exact_keys(
        credential_document,
        _CREDENTIAL_KEYS,
        "credential_keys_invalid",
    )
    return _parse_auth_config(config_document, credential_document)


def build_auth0_provider(
    *,
    config: ClaudeWebRemoteAuthConfig,
    expected_owner_uid: int,
    jwks_client: Any | None = None,
    oidc_configuration: OIDCConfiguration | None = None,
) -> RestrictedAuth0Provider:
    """Build Claude's provider after validating its independent bindings."""
    _validate_config_bindings(config)
    return build_scoped_auth0_provider(
        config=config,  # type: ignore[arg-type]  # Same immutable config shape.
        expected_owner_uid=expected_owner_uid,
        access_scope=NOTES_ACCESS_SCOPE,
        jwks_client=jwks_client,
        oidc_configuration=oidc_configuration,
    )


def _parse_auth_config(
    public: Mapping[str, Any],
    secret: Mapping[str, Any],
) -> ClaudeWebRemoteAuthConfig:
    config = ClaudeWebRemoteAuthConfig(
        issuer_url=_required_string(public, "issuer_url"),
        jwks_url=_required_string(public, "jwks_url"),
        auth0_config_url=_required_string(public, "auth0_config_url"),
        auth0_client_id=_required_string(secret, "auth0_client_id"),
        auth0_client_secret=_required_string(secret, "auth0_client_secret"),
        proxy_base_url=_required_string(public, "proxy_base_url"),
        audience=_required_string(public, "audience"),
        oauth_storage_directory=Path(
            _required_string(public, "oauth_storage_directory")
        ),
        allowed_client_redirect_uris=_required_string_list(
            public,
            "allowed_client_redirect_uris",
            "allowed_client_redirect_uris_invalid",
        ),
        allowed_subjects=frozenset(
            _required_string_list(
                secret,
                "allowed_subjects",
                "allowed_subjects_invalid",
            )
        ),
        storage_encryption_key=_required_string(secret, "storage_encryption_key"),
        jwt_signing_key=_required_string(secret, "jwt_signing_key"),
    )
    _validate_config_bindings(config)
    return config


def _validate_config_bindings(config: ClaudeWebRemoteAuthConfig) -> None:
    issuer = _validated_https_url(config.issuer_url, "issuer_url_invalid")
    if not issuer.endswith("/"):
        raise ClaudeWebRemoteAuthError("issuer_url_invalid")
    if config.auth0_config_url != f"{issuer}.well-known/openid-configuration":
        raise ClaudeWebRemoteAuthError("auth0_config_url_mismatch")
    if config.jwks_url != f"{issuer}.well-known/jwks.json":
        raise ClaudeWebRemoteAuthError("jwks_url_mismatch")
    _validated_https_url(config.auth0_config_url, "auth0_config_url_invalid")
    _validated_https_url(config.jwks_url, "jwks_url_invalid")
    if config.proxy_base_url != PROXY_BASE_URL:
        raise ClaudeWebRemoteAuthError("proxy_base_url_mismatch")
    if config.audience != AUDIENCE:
        raise ClaudeWebRemoteAuthError("audience_mismatch")
    if config.oauth_storage_directory != OAUTH_STORAGE_DIRECTORY:
        raise ClaudeWebRemoteAuthError("oauth_storage_directory_mismatch")
    if (
        len(config.allowed_client_redirect_uris) != len(ALLOWED_REDIRECT_URIS)
        or frozenset(config.allowed_client_redirect_uris) != ALLOWED_REDIRECT_URIS
    ):
        raise ClaudeWebRemoteAuthError("allowed_client_redirect_uris_invalid")
    if not config.allowed_subjects:
        raise ClaudeWebRemoteAuthError("allowed_subjects_missing")
