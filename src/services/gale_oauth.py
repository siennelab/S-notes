from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from typing import Any

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

from src.services.gale_remote_service import GALE_READ_SCOPE


LOGGER = logging.getLogger("snotes_gale_mcp.auth")
ALLOWED_IDENTITY_SCOPES = {
    "openid",
    "profile",
    "email",
    "offline_access",
}
ALLOWED_ACCESS_SCOPES = {GALE_READ_SCOPE} | ALLOWED_IDENTITY_SCOPES


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


def _token_scopes(claims: dict[str, Any]) -> set[str]:
    value = claims.get("scope")
    if not isinstance(value, str):
        return set()
    return {scope for scope in value.split() if scope}


def _safe_auth_failure(code: str) -> None:
    LOGGER.warning("auth_event status=failed error_code=%s", code)
