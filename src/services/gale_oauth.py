from __future__ import annotations

import asyncio
import logging
import os
import stat
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import jwt
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.auth0 import Auth0Provider
from jwt import PyJWKClient
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.provider import AuthorizeError, TokenError
from starlette.requests import Request
from starlette.responses import HTMLResponse

from src.services.gale_remote_service import GALE_READ_SCOPE


LOGGER = logging.getLogger("snotes_gale_mcp.auth")
ALLOWED_IDENTITY_SCOPES = {
    "openid",
    "profile",
    "email",
    "offline_access",
}
ALLOWED_ACCESS_SCOPES = {
    GALE_READ_SCOPE,
} | ALLOWED_IDENTITY_SCOPES
RUNTIME_REQUIRED_SCOPES = [
    GALE_READ_SCOPE,
]
UPSTREAM_REQUESTED_SCOPES = [
    "openid",
    "offline_access",
    GALE_READ_SCOPE,
]
FIXED_AUTH_CALLBACK_PATH = "/auth/callback"


class GaleOAuthConfigError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GaleOAuthProxyConfig:
    auth0_config_url: str
    auth0_client_id: str
    auth0_client_secret: str
    proxy_base_url: str
    storage_directory: Path
    storage_encryption_key: str
    jwt_signing_key: str
    allowed_client_redirect_uris: tuple[str, ...]


class Auth0TokenVerifier(TokenVerifier):
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        allowed_subjects: Iterable[str],
        jwks_client: Any | None = None,
    ) -> None:
        super().__init__(required_scopes=RUNTIME_REQUIRED_SCOPES)
        self.issuer = issuer
        self.audience = audience
        self.allowed_subjects = frozenset(allowed_subjects)
        if not self.allowed_subjects:
            raise ValueError("allowed_subjects_missing")
        self._jwks_client = jwks_client or PyJWKClient(
            jwks_url,
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = await asyncio.to_thread(
                self._jwks_client.get_signing_key_from_jwt,
                token,
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=30,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
            subject = str(claims.get("sub") or "")
            if subject not in self.allowed_subjects:
                _safe_auth_failure("subject_rejected")
                return None
            scopes = _token_scopes(claims)
            if GALE_READ_SCOPE not in scopes:
                _safe_auth_failure("scope_missing")
                return None
            if not scopes.issubset(ALLOWED_ACCESS_SCOPES):
                _safe_auth_failure("scope_not_allowed")
                return None
            client_id = str(
                claims.get("azp") or claims.get("client_id") or ""
            )
            if not client_id:
                _safe_auth_failure("client_id_missing")
                return None
            expires_at = int(claims["exp"])
            if expires_at <= int(time.time()):
                _safe_auth_failure("token_expired")
                return None
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=sorted(scopes),
                expires_at=expires_at,
                resource=self.audience,
                subject=subject,
                claims={
                    "iss": str(claims["iss"]),
                    "aud": claims["aud"],
                    "sub": subject,
                },
            )
        except Exception:
            _safe_auth_failure("token_rejected")
            return None


class RestrictedAuth0Provider(Auth0Provider):
    """Auth0 OIDC proxy with the existing Gale subject-aware verifier."""

    def __init__(
        self,
        *,
        allowed_subjects: Iterable[str],
        jwks_client: Any | None = None,
        oidc_configuration: OIDCConfiguration | None = None,
        **kwargs: Any,
    ) -> None:
        self._allowed_subjects = frozenset(allowed_subjects)
        self._jwks_client = jwks_client
        self._oidc_configuration = oidc_configuration
        if not self._allowed_subjects:
            raise ValueError("allowed_subjects_missing")
        super().__init__(**kwargs)
        if self.client_registration_options is None:
            raise ValueError("client_registration_options_missing")
        self.client_registration_options.valid_scopes = [GALE_READ_SCOPE]
        self.client_registration_options.default_scopes = [GALE_READ_SCOPE]

    def get_oidc_configuration(
        self,
        config_url,
        strict: bool | None,
        timeout_seconds: int | None,
    ) -> OIDCConfiguration:
        if self._oidc_configuration is not None:
            return self._oidc_configuration
        return super().get_oidc_configuration(
            config_url,
            strict,
            timeout_seconds,
        )

    def get_token_verifier(
        self,
        *,
        algorithm: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> TokenVerifier:
        del algorithm, required_scopes, timeout_seconds
        if not audience:
            raise ValueError("audience_missing")
        return Auth0TokenVerifier(
            issuer=str(self.oidc_config.issuer),
            audience=audience,
            jwks_url=str(self.oidc_config.jwks_uri),
            allowed_subjects=self._allowed_subjects,
            jwks_client=self._jwks_client,
        )

    async def authorize(self, client: Any, params: Any) -> str:
        try:
            return await super().authorize(client, params)
        except AuthorizeError as error:
            # FastMCP uses RFC 8707's "invalid_target", while MCP SDK 1.28.1
            # cannot serialize that value. Preserve the fail-closed decision
            # with an interoperable OAuth error and no framework traceback.
            if error.error == "invalid_target":
                raise AuthorizeError(
                    error="invalid_request",
                    error_description="resource_mismatch",
                ) from error
            raise

    def _build_upstream_authorize_url(
        self,
        txn_id: str,
        transaction: dict[str, Any],
    ) -> str:
        upstream_transaction = dict(transaction)
        requested_scopes = list(transaction.get("scopes") or [])
        for scope in UPSTREAM_REQUESTED_SCOPES:
            if scope not in requested_scopes:
                requested_scopes.append(scope)
        upstream_transaction["scopes"] = requested_scopes
        return super()._build_upstream_authorize_url(
            txn_id,
            upstream_transaction,
        )

    async def _handle_idp_callback(self, request: Request) -> Any:
        response = await super()._handle_idp_callback(request)
        if response.status_code >= 500:
            _safe_proxy_failure(
                "upstream_token_exchange_failed",
                response.status_code,
            )
            return HTMLResponse(
                "OAuth token exchange failed.",
                status_code=response.status_code,
            )
        return response

    async def exchange_refresh_token(
        self,
        client: Any,
        refresh_token: Any,
        scopes: list[str],
    ) -> Any:
        try:
            return await super().exchange_refresh_token(
                client,
                refresh_token,
                scopes,
            )
        except TokenError as error:
            _safe_proxy_failure("upstream_refresh_failed", 400)
            raise TokenError(
                "invalid_grant",
                "upstream_refresh_failed",
            ) from error


def load_gale_oauth_proxy_config(
    environment: dict[str, str] | None = None,
) -> GaleOAuthProxyConfig:
    values = os.environ if environment is None else environment
    storage_directory = Path(
        _required_value(values, "SNOTES_GALE_OAUTH_STORAGE_DIR")
    ).expanduser()
    allowed_redirects = tuple(
        item.strip()
        for item in _required_value(
            values,
            "SNOTES_GALE_ALLOWED_CLIENT_REDIRECT_URIS",
        ).split(",")
        if item.strip()
    )
    if not allowed_redirects:
        raise GaleOAuthConfigError("allowed_client_redirect_uris_missing")
    return GaleOAuthProxyConfig(
        auth0_config_url=_validated_url(
            _required_value(values, "SNOTES_GALE_AUTH0_CONFIG_URL"),
            allow_loopback_http=False,
        ),
        auth0_client_id=_required_value(
            values,
            "SNOTES_GALE_AUTH0_CLIENT_ID",
        ),
        auth0_client_secret=_required_value(
            values,
            "SNOTES_GALE_AUTH0_CLIENT_SECRET",
        ),
        proxy_base_url=_validated_url(
            _required_value(values, "SNOTES_GALE_PROXY_BASE_URL"),
            allow_loopback_http=True,
        ),
        storage_directory=storage_directory,
        storage_encryption_key=_required_value(
            values,
            "SNOTES_GALE_STORAGE_ENCRYPTION_KEY",
        ),
        jwt_signing_key=_required_value(
            values,
            "SNOTES_GALE_PROXY_JWT_SIGNING_KEY",
        ),
        allowed_client_redirect_uris=allowed_redirects,
    )


def build_auth0_provider(
    *,
    oauth_config: GaleOAuthProxyConfig,
    issuer_url: str,
    audience: str,
    jwks_url: str,
    allowed_subjects: Iterable[str],
    expected_owner_uid: int,
    jwks_client: Any | None = None,
    oidc_configuration: OIDCConfiguration | None = None,
) -> RestrictedAuth0Provider:
    storage_directory = _validate_storage_directory(
        oauth_config.storage_directory,
        expected_owner_uid=expected_owner_uid,
    )
    try:
        fernet = Fernet(oauth_config.storage_encryption_key.encode("ascii"))
    except Exception as error:
        raise GaleOAuthConfigError("storage_encryption_key_invalid") from error
    if len(oauth_config.jwt_signing_key) < 32:
        raise GaleOAuthConfigError("jwt_signing_key_too_short")
    _validated_url(
        oauth_config.auth0_config_url,
        allow_loopback_http=True,
    )
    _validated_url(
        oauth_config.proxy_base_url,
        allow_loopback_http=True,
    )
    for redirect_uri in oauth_config.allowed_client_redirect_uris:
        _validate_redirect_uri_pattern(redirect_uri)

    file_store = FileTreeStore(
        data_directory=storage_directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(
            storage_directory
        ),
        collection_sanitization_strategy=(
            FileTreeV1CollectionSanitizationStrategy(storage_directory)
        ),
        auto_create=True,
    )
    encrypted_storage = FernetEncryptionWrapper(
        key_value=file_store,
        fernet=fernet,
        raise_on_decryption_error=True,
    )
    provider = RestrictedAuth0Provider(
        config_url=oauth_config.auth0_config_url,
        client_id=oauth_config.auth0_client_id,
        client_secret=oauth_config.auth0_client_secret,
        audience=audience,
        base_url=oauth_config.proxy_base_url,
        resource_base_url=oauth_config.proxy_base_url,
        issuer_url=oauth_config.proxy_base_url,
        required_scopes=RUNTIME_REQUIRED_SCOPES,
        redirect_path=FIXED_AUTH_CALLBACK_PATH,
        allowed_client_redirect_uris=list(
            oauth_config.allowed_client_redirect_uris
        ),
        client_storage=encrypted_storage,
        jwt_signing_key=oauth_config.jwt_signing_key,
        require_authorization_consent=True,
        forward_resource=True,
        allowed_subjects=allowed_subjects,
        jwks_client=jwks_client,
        oidc_configuration=oidc_configuration,
    )
    discovered_issuer = str(provider.oidc_config.issuer).rstrip("/") + "/"
    expected_issuer = issuer_url.rstrip("/") + "/"
    if discovered_issuer != expected_issuer:
        raise GaleOAuthConfigError("auth0_issuer_mismatch")
    if str(provider.oidc_config.jwks_uri) != jwks_url:
        raise GaleOAuthConfigError("auth0_jwks_mismatch")
    return provider


def _token_scopes(claims: dict[str, Any]) -> set[str]:
    value = claims.get("scope")
    if not isinstance(value, str):
        return set()
    return {scope for scope in value.split() if scope}


def _safe_auth_failure(code: str) -> None:
    LOGGER.warning("auth_event status=failed error_code=%s", code)


def _safe_proxy_failure(code: str, http_status: int) -> None:
    LOGGER.warning(
        "oauth_proxy_event status=failed error_code=%s http_status=%d",
        code,
        http_status,
    )


def _required_value(
    values: dict[str, str] | os._Environ[str],
    name: str,
) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise GaleOAuthConfigError(f"{name.lower()}_missing")
    return value


def _validate_storage_directory(
    directory: Path,
    *,
    expected_owner_uid: int,
) -> Path:
    original = Path(directory)
    if original.is_symlink():
        raise GaleOAuthConfigError("oauth_storage_symlink_rejected")
    try:
        resolved = original.resolve(strict=True)
        file_stat = resolved.stat()
    except OSError as error:
        raise GaleOAuthConfigError("oauth_storage_unavailable") from error
    if not resolved.is_dir():
        raise GaleOAuthConfigError("oauth_storage_not_directory")
    if stat.S_IMODE(file_stat.st_mode) != 0o700:
        raise GaleOAuthConfigError("oauth_storage_permissions_unsafe")
    if file_stat.st_uid != expected_owner_uid:
        raise GaleOAuthConfigError("oauth_storage_owner_unsafe")
    return resolved


def _validated_url(value: str, *, allow_loopback_http: bool) -> str:
    parsed = urlparse(value)
    loopback_http = (
        allow_loopback_http
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    )
    if (
        (parsed.scheme != "https" and not loopback_http)
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GaleOAuthConfigError("oauth_url_invalid")
    return value


def _validate_redirect_uri_pattern(value: str) -> None:
    parsed = urlparse(value)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme == "http" and not loopback)
    ):
        raise GaleOAuthConfigError("client_redirect_uri_unsafe")
