"""Fail-closed Auth0 OAuth proxy for Gale's GreenCloud MCP endpoint."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import pwd
import stat
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from fastmcp.server.auth.providers.auth0 import Auth0Provider
import jwt
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
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from src.snotes.transport.mcp.remote_auth_support import (
    RemoteAuthError,
    load_json_file,
    require_exact_keys,
    required_string,
    required_string_list,
    validated_https_url,
)


LOGGER = logging.getLogger("snotes_greencloud_remote_mcp.auth")

GALE_READ_SCOPE = "gale.read"
CONFIG_PATH = Path("/srv/snotes/gale-remote/config.json")
CREDENTIAL_NAME = "remote-oauth.json"
RUNTIME_ACCOUNT = "snotes-gale"
PROXY_BASE_URL = "https://snotes-gale.ke-den.de"
AUDIENCE = f"{PROXY_BASE_URL}/mcp"
OAUTH_STORAGE_DIRECTORY = Path("/srv/snotes/gale-remote/oauth")
CHATGPT_REDIRECT_URI = "https://chatgpt.com/connector_platform_oauth_redirect"
LOOPBACK_REDIRECT_PATTERN = "http://127.0.0.1:*"
ALLOWED_REDIRECT_URIS = frozenset(
    {CHATGPT_REDIRECT_URI, LOOPBACK_REDIRECT_PATTERN}
)
FIXED_AUTH_CALLBACK_PATH = "/auth/callback"
MAX_CONFIG_BYTES = 64 * 1024
MAX_CREDENTIAL_BYTES = 64 * 1024

ALLOWED_IDENTITY_SCOPES = {
    "openid",
    "profile",
    "email",
    "offline_access",
}
ALLOWED_ACCESS_SCOPES = {GALE_READ_SCOPE} | ALLOWED_IDENTITY_SCOPES
RUNTIME_REQUIRED_SCOPES = [GALE_READ_SCOPE]
UPSTREAM_REQUESTED_SCOPES = ["openid", "offline_access", GALE_READ_SCOPE]

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


GreenCloudRemoteAuthError = RemoteAuthError


@dataclass(frozen=True)
class GreenCloudRemoteAuthConfig:
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


class Auth0TokenVerifier(TokenVerifier):
    """Verify an Auth0 access token against one issuer, audience and owner."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        allowed_subjects: Iterable[str],
        access_scope: str = GALE_READ_SCOPE,
        jwks_client: Any | None = None,
    ) -> None:
        if not access_scope or access_scope in ALLOWED_IDENTITY_SCOPES:
            raise ValueError("access_scope_invalid")
        super().__init__(required_scopes=[access_scope])
        self.issuer = issuer
        self.audience = audience
        self.access_scope = access_scope
        self.allowed_access_scopes = {access_scope} | ALLOWED_IDENTITY_SCOPES
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
            if self.access_scope not in scopes:
                _safe_auth_failure("scope_missing")
                return None
            if not scopes.issubset(self.allowed_access_scopes):
                _safe_auth_failure("scope_not_allowed")
                return None
            client_id = str(claims.get("azp") or claims.get("client_id") or "")
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
    """Auth0 proxy with fixed claim restrictions and RFC 9207 responses."""

    def __init__(
        self,
        *,
        allowed_subjects: Iterable[str],
        access_scope: str = GALE_READ_SCOPE,
        jwks_client: Any | None = None,
        oidc_configuration: OIDCConfiguration | None = None,
        **kwargs: Any,
    ) -> None:
        self._allowed_subjects = frozenset(allowed_subjects)
        if not access_scope or access_scope in ALLOWED_IDENTITY_SCOPES:
            raise ValueError("access_scope_invalid")
        self._access_scope = access_scope
        self._jwks_client = jwks_client
        self._oidc_configuration = oidc_configuration
        if not self._allowed_subjects:
            raise ValueError("allowed_subjects_missing")
        super().__init__(**kwargs)
        if self.client_registration_options is None:
            raise ValueError("client_registration_options_missing")
        self.client_registration_options.valid_scopes = [self._access_scope]
        self.client_registration_options.default_scopes = [self._access_scope]

    def get_oidc_configuration(
        self,
        config_url: str,
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
            access_scope=self._access_scope,
            jwks_client=self._jwks_client,
        )

    async def authorize(self, client: Any, params: Any) -> str:
        try:
            return await super().authorize(client, params)
        except AuthorizeError as error:
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
        for scope in ("openid", "offline_access", self._access_scope):
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
            _safe_proxy_failure("upstream_token_exchange_failed", response.status_code)
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
            raise TokenError("invalid_grant", "upstream_refresh_failed") from error

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Add RFC 9207 metadata and issuer to local downstream responses."""
        routes = super().get_routes(mcp_path)
        wrapped: list[Route] = []
        # AnyHttpUrl canonicalizes an origin issuer with a trailing slash. RFC
        # 9207 requires byte-for-byte equality with the advertised issuer.
        issuer = str(self.base_url)
        for route in routes:
            if not isinstance(route, Route):
                wrapped.append(route)
                continue
            metadata = route.path.startswith(
                "/.well-known/oauth-authorization-server"
            )
            if not metadata and route.path not in {
                "/authorize",
                "/consent",
                FIXED_AUTH_CALLBACK_PATH,
            }:
                wrapped.append(route)
                continue
            endpoint = _RFC9207RouteApp(
                route.app, provider=self, issuer=issuer, metadata=metadata
            )
            wrapped.append(
                Route(
                    path=route.path,
                    endpoint=endpoint,
                    methods=route.methods,
                    name=route.name,
                    include_in_schema=route.include_in_schema,
                )
            )
        return wrapped


def build_auth_provider() -> RestrictedAuth0Provider:
    """Load the fixed production files and build Gale's auth provider."""
    try:
        runtime_uid = pwd.getpwnam(RUNTIME_ACCOUNT).pw_uid
    except KeyError as error:
        raise GreenCloudRemoteAuthError("runtime_account_missing") from error
    if os.geteuid() != runtime_uid:
        raise GreenCloudRemoteAuthError("runtime_account_mismatch")
    config = load_auth_config(expected_owner_uid=runtime_uid)
    return build_auth0_provider(
        config=config,
        expected_owner_uid=runtime_uid,
    )


def load_auth_config(*, expected_owner_uid: int) -> GreenCloudRemoteAuthConfig:
    """Load fixed non-secret config and one systemd credential."""
    config_document = load_json_file(
        CONFIG_PATH,
        maximum_bytes=MAX_CONFIG_BYTES,
        error_prefix="config",
        allowed_modes={0o600, 0o644},
        allowed_owner_uids={0},
    )
    require_exact_keys(config_document, _CONFIG_KEYS, "config_keys_invalid")

    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if not credentials_directory:
        raise GreenCloudRemoteAuthError("credentials_directory_missing")
    directory = Path(credentials_directory)
    if not directory.is_absolute():
        raise GreenCloudRemoteAuthError("credentials_directory_not_absolute")
    credential_document = load_json_file(
        directory / CREDENTIAL_NAME,
        maximum_bytes=MAX_CREDENTIAL_BYTES,
        error_prefix="credential",
        # systemd may expose LoadCredential files as root:root 0440 inside
        # the unit's private credential mount, granting the service an ACL.
        allowed_modes={0o400, 0o440, 0o600},
        allowed_owner_uids={0, expected_owner_uid},
    )
    require_exact_keys(
        credential_document,
        _CREDENTIAL_KEYS,
        "credential_keys_invalid",
    )
    return _parse_auth_config(config_document, credential_document)


def build_auth0_provider(
    *,
    config: GreenCloudRemoteAuthConfig,
    expected_owner_uid: int,
    jwks_client: Any | None = None,
    oidc_configuration: OIDCConfiguration | None = None,
) -> RestrictedAuth0Provider:
    """Build a provider; optional network objects support fictional tests only."""
    _validate_config_bindings(config)
    return build_scoped_auth0_provider(
        config=config,
        expected_owner_uid=expected_owner_uid,
        access_scope=GALE_READ_SCOPE,
        jwks_client=jwks_client,
        oidc_configuration=oidc_configuration,
    )


def build_scoped_auth0_provider(
    *,
    config: GreenCloudRemoteAuthConfig,
    expected_owner_uid: int,
    access_scope: str,
    jwks_client: Any | None = None,
    oidc_configuration: OIDCConfiguration | None = None,
) -> RestrictedAuth0Provider:
    """Build a scoped provider after the caller validates fixed bindings."""
    if not access_scope or access_scope in ALLOWED_IDENTITY_SCOPES:
        raise GreenCloudRemoteAuthError("access_scope_invalid")
    storage_directory = _validate_storage_directory(
        config.oauth_storage_directory,
        expected_owner_uid=expected_owner_uid,
    )
    try:
        fernet = Fernet(config.storage_encryption_key.encode("ascii"))
    except Exception as error:
        raise GreenCloudRemoteAuthError(
            "storage_encryption_key_invalid"
        ) from error
    if len(config.jwt_signing_key) < 32:
        raise GreenCloudRemoteAuthError("jwt_signing_key_too_short")

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
        config_url=config.auth0_config_url,
        client_id=config.auth0_client_id,
        client_secret=config.auth0_client_secret,
        audience=config.audience,
        base_url=config.proxy_base_url,
        resource_base_url=config.proxy_base_url,
        issuer_url=config.proxy_base_url,
        required_scopes=[access_scope],
        redirect_path=FIXED_AUTH_CALLBACK_PATH,
        allowed_client_redirect_uris=list(
            config.allowed_client_redirect_uris
        ),
        client_storage=encrypted_storage,
        jwt_signing_key=config.jwt_signing_key,
        require_authorization_consent=True,
        forward_resource=True,
        allowed_subjects=config.allowed_subjects,
        access_scope=access_scope,
        jwks_client=jwks_client,
        oidc_configuration=oidc_configuration,
    )
    discovered_issuer = str(provider.oidc_config.issuer).rstrip("/") + "/"
    if discovered_issuer != config.issuer_url:
        raise GreenCloudRemoteAuthError("auth0_issuer_mismatch")
    if str(provider.oidc_config.jwks_uri) != config.jwks_url:
        raise GreenCloudRemoteAuthError("auth0_jwks_mismatch")
    return provider


def _parse_auth_config(
    public: Mapping[str, Any],
    secret: Mapping[str, Any],
) -> GreenCloudRemoteAuthConfig:
    redirects = required_string_list(
        public,
        "allowed_client_redirect_uris",
        "allowed_client_redirect_uris_invalid",
    )
    subjects = required_string_list(
        secret,
        "allowed_subjects",
        "allowed_subjects_invalid",
    )
    config = GreenCloudRemoteAuthConfig(
        issuer_url=required_string(public, "issuer_url"),
        jwks_url=required_string(public, "jwks_url"),
        auth0_config_url=required_string(public, "auth0_config_url"),
        auth0_client_id=required_string(secret, "auth0_client_id"),
        auth0_client_secret=required_string(secret, "auth0_client_secret"),
        proxy_base_url=required_string(public, "proxy_base_url"),
        audience=required_string(public, "audience"),
        oauth_storage_directory=Path(
            required_string(public, "oauth_storage_directory")
        ),
        allowed_client_redirect_uris=redirects,
        allowed_subjects=frozenset(subjects),
        storage_encryption_key=required_string(
            secret,
            "storage_encryption_key",
        ),
        jwt_signing_key=required_string(secret, "jwt_signing_key"),
    )
    _validate_config_bindings(config)
    return config


def _validate_config_bindings(config: GreenCloudRemoteAuthConfig) -> None:
    issuer = validated_https_url(config.issuer_url, "issuer_url_invalid")
    if not issuer.endswith("/"):
        raise GreenCloudRemoteAuthError("issuer_url_invalid")
    expected_config = f"{issuer}.well-known/openid-configuration"
    expected_jwks = f"{issuer}.well-known/jwks.json"
    if config.auth0_config_url != expected_config:
        raise GreenCloudRemoteAuthError("auth0_config_url_mismatch")
    if config.jwks_url != expected_jwks:
        raise GreenCloudRemoteAuthError("jwks_url_mismatch")
    validated_https_url(config.auth0_config_url, "auth0_config_url_invalid")
    validated_https_url(config.jwks_url, "jwks_url_invalid")
    if config.proxy_base_url != PROXY_BASE_URL:
        raise GreenCloudRemoteAuthError("proxy_base_url_mismatch")
    if config.audience != AUDIENCE:
        raise GreenCloudRemoteAuthError("audience_mismatch")
    if config.oauth_storage_directory != OAUTH_STORAGE_DIRECTORY:
        raise GreenCloudRemoteAuthError("oauth_storage_directory_mismatch")
    if (
        len(config.allowed_client_redirect_uris)
        != len(ALLOWED_REDIRECT_URIS)
        or frozenset(config.allowed_client_redirect_uris)
        != ALLOWED_REDIRECT_URIS
    ):
        raise GreenCloudRemoteAuthError("allowed_client_redirect_uris_invalid")
    if not config.allowed_subjects:
        raise GreenCloudRemoteAuthError("allowed_subjects_missing")


def _validate_storage_directory(
    directory: Path,
    *,
    expected_owner_uid: int,
) -> Path:
    original = Path(directory)
    if original.is_symlink():
        raise GreenCloudRemoteAuthError("oauth_storage_symlink_rejected")
    try:
        resolved = original.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise GreenCloudRemoteAuthError("oauth_storage_unavailable") from error
    if not resolved.is_dir():
        raise GreenCloudRemoteAuthError("oauth_storage_not_directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise GreenCloudRemoteAuthError("oauth_storage_permissions_unsafe")
    if metadata.st_uid != expected_owner_uid:
        raise GreenCloudRemoteAuthError("oauth_storage_owner_unsafe")
    return resolved


class _RFC9207RouteApp:
    """Adapt either request handlers or ASGI middleware without bypassing CORS."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        provider: RestrictedAuth0Provider,
        issuer: str,
        metadata: bool,
    ) -> None:
        self._app = app
        self._provider = provider
        self._issuer = issuer
        self._metadata = metadata

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        messages: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            messages.append(message)

        await self._app(scope, receive, capture)
        start = next(
            (item for item in messages if item["type"] == "http.response.start"),
            None,
        )
        if start is None:
            for message in messages:
                await send(message)
            return
        body = b"".join(
            item.get("body", b"")
            for item in messages
            if item["type"] == "http.response.body"
        )
        status = int(start["status"])
        headers = list(start.get("headers", []))

        if self._metadata and status == 200:
            body = _rfc9207_metadata_body(body, self._issuer)
            headers = _replace_raw_header(
                headers,
                b"content-length",
                str(len(body)).encode("ascii"),
            )

        location = _raw_header(headers, b"location")
        if location is not None and status in {302, 303, 307, 308}:
            updated = _rfc9207_redirect_location(
                location.decode("latin-1"),
                self._provider,
                self._issuer,
            )
            headers = _replace_raw_header(
                headers,
                b"location",
                updated.encode("latin-1"),
            )

        await send({**start, "headers": headers})
        await send(
            {
                "type": "http.response.body",
                "body": body,
                "more_body": False,
            }
        )


def _rfc9207_metadata_body(body: bytes, issuer: str) -> bytes:
    try:
        document = json.loads(body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GreenCloudRemoteAuthError(
            "authorization_metadata_invalid"
        ) from error
    if not isinstance(document, dict) or document.get("issuer") != issuer:
        raise GreenCloudRemoteAuthError("authorization_metadata_issuer_mismatch")
    document["authorization_response_iss_parameter_supported"] = True
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _rfc9207_redirect_location(
    location: str,
    provider: RestrictedAuth0Provider,
    issuer: str,
) -> str:
    if not provider._validate_client_redirect_uri(location):
        return location
    parsed = urlsplit(location)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    names = [name for name, _value in query]
    if "code" not in names and "error" not in names:
        return location
    existing = [value for name, value in query if name == "iss"]
    if existing and (len(existing) != 1 or existing[0] != issuer):
        raise GreenCloudRemoteAuthError("authorization_response_issuer_invalid")
    if not existing:
        query.append(("iss", issuer))
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )
    return location


def _raw_header(
    headers: list[tuple[bytes, bytes]],
    name: bytes,
) -> bytes | None:
    lowered = name.lower()
    for header_name, value in headers:
        if header_name.lower() == lowered:
            return value
    return None


def _replace_raw_header(
    headers: list[tuple[bytes, bytes]],
    name: bytes,
    value: bytes,
) -> list[tuple[bytes, bytes]]:
    lowered = name.lower()
    return [
        (header_name, header_value)
        for header_name, header_value in headers
        if header_name.lower() != lowered
    ] + [(name, value)]


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
