"""Human OIDC login for the single-user Reader; no MCP OAuth state or scopes."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt


ORIGIN = "https://notes.ke-den.de"
MIRROR_PATH = Path("/srv/snotes/mirror/s/current.db")
SESSION_COOKIE = "__Host-snotes-reader"
TRANSACTION_COOKIE = "__Host-snotes-reader-login"
SESSION_TTL = 7 * 24 * 60 * 60
TRANSACTION_TTL = 10 * 60


class LoginError(ValueError):
    """Safe, deliberately non-diagnostic public login failure."""


@dataclass(frozen=True)
class ReaderSettings:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    allowed_sub: str = field(repr=False)
    port: int = 0

    def __post_init__(self):
        parsed = urlsplit(self.issuer)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.port or parsed.path != "/"
                or parsed.query or parsed.fragment):
            raise ValueError("invalid_reader_issuer")
        if not all(isinstance(value, str) and value.strip() for value in (
            self.client_id, self.client_secret, self.allowed_sub,
        )):
            raise ValueError("invalid_reader_identity_configuration")


def _read_config(path: Path, *, secret: bool) -> dict:
    metadata = path.lstat()
    modes = {0o400, 0o440, 0o600} if secret else {0o600, 0o644}
    owners = {0, os.geteuid()} if secret else {0}
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid not in owners
            or stat.S_IMODE(metadata.st_mode) not in modes or metadata.st_size > 16384):
        raise ValueError("unsafe_reader_configuration")
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings() -> ReaderSettings:
    public = _read_config(Path("/srv/snotes/reader/config.json"), secret=False)
    directory = Path(os.environ["CREDENTIALS_DIRECTORY"])
    if not directory.is_absolute():
        raise ValueError("invalid_credentials_directory")
    private = _read_config(directory / "reader-oidc.json", secret=True)
    if set(public) != {"issuer", "client_id", "port"} or set(private) != {"client_secret", "allowed_sub"}:
        raise ValueError("invalid_reader_configuration_keys")
    if type(public["port"]) is not int or not 1024 <= public["port"] <= 65535:
        raise ValueError("invalid_reader_port")
    return ReaderSettings(**public, **private)


@dataclass
class Transaction:
    nonce: str
    verifier: str
    binding: str
    expires: float


@dataclass
class Session:
    csrf: str
    expires: float


class ReaderLogin:
    """Bound, one-use OIDC transactions and opaque absolute-lifetime sessions."""

    def __init__(self, settings: ReaderSettings, *, clock=time.monotonic):
        self.settings = settings
        self.clock = clock
        self.pending: dict[str, Transaction] = {}
        self.sessions: dict[str, Session] = {}

    def _prune(self):
        now = self.clock()
        self.pending = {key: value for key, value in self.pending.items() if value.expires > now}
        self.sessions = {key: value for key, value in self.sessions.items() if value.expires > now}

    def begin(self) -> tuple[str, str]:
        self._prune()
        if len(self.pending) >= 100 or len(self.sessions) >= 100:
            raise LoginError("login_unavailable")
        state, nonce, verifier, binding = (secrets.token_urlsafe(32) for _ in range(4))
        self.pending[state] = Transaction(nonce, verifier, binding, self.clock() + TRANSACTION_TTL)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        return self.settings.issuer + "authorize?" + urlencode({
            "client_id": self.settings.client_id, "redirect_uri": ORIGIN + "/auth/callback",
            "response_type": "code", "scope": "openid", "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }), binding

    def consume(self, state: str, binding: str) -> Transaction:
        self._prune()
        transaction = self.pending.pop(state, None)
        if transaction is None or not secrets.compare_digest(transaction.binding, binding):
            raise LoginError("login_failed")
        return transaction

    def establish(self) -> str:
        self._prune()
        if len(self.sessions) >= 100:
            raise LoginError("login_unavailable")
        session_id = secrets.token_urlsafe(48)
        self.sessions[session_id] = Session(secrets.token_urlsafe(32), self.clock() + SESSION_TTL)
        return session_id

    def session(self, session_id: str) -> Session | None:
        self._prune()
        return self.sessions.get(session_id)


class Auth0OIDC:
    def __init__(self, settings: ReaderSettings, *, transport=None):
        self.settings = settings
        self.transport = transport
        self._jwks: dict = {}
        self._jwks_expires = 0.0

    def verify_id_token(self, token: str, nonce: str, jwks: dict) -> None:
        try:
            if not isinstance(token, str) or len(token) > 16384:
                raise LoginError("login_failed")
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise LoginError("login_failed")
            keys = [key for key in jwks["keys"] if key.get("kid") == header["kid"]
                    and key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
                    and key.get("alg", "RS256") == "RS256"]
            if len(keys) != 1:
                raise LoginError("login_failed")
            claims = jwt.decode(
                token, jwt.PyJWK.from_dict(keys[0]).key, algorithms=["RS256"],
                audience=self.settings.client_id, issuer=self.settings.issuer,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"], "strict_aud": True},
            )
            if (not isinstance(claims["nonce"], str)
                    or not secrets.compare_digest(claims["nonce"], nonce)
                    or not secrets.compare_digest(claims["sub"], self.settings.allowed_sub)
                    or claims.get("azp", self.settings.client_id) != self.settings.client_id):
                raise LoginError("login_failed")
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
            raise LoginError("login_failed") from None

    async def complete(self, code: str, transaction: Transaction) -> None:
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False, transport=self.transport) as client:
                response = await client.post(self.settings.issuer + "oauth/token", data={
                    "grant_type": "authorization_code", "client_id": self.settings.client_id,
                    "client_secret": self.settings.client_secret, "code": code,
                    "code_verifier": transaction.verifier, "redirect_uri": ORIGIN + "/auth/callback",
                })
                response.raise_for_status()
                token = response.json()["id_token"]
                if time.monotonic() >= self._jwks_expires:
                    keys = await client.get(self.settings.issuer + ".well-known/jwks.json")
                    keys.raise_for_status()
                    self._jwks, self._jwks_expires = keys.json(), time.monotonic() + 300
                # A new signing kid can legitimately appear during key rotation.
                if jwt.get_unverified_header(token).get("kid") not in {key.get("kid") for key in self._jwks.get("keys", [])}:
                    keys = await client.get(self.settings.issuer + ".well-known/jwks.json")
                    keys.raise_for_status()
                    self._jwks = keys.json()
                self.verify_id_token(token, transaction.nonce, self._jwks)
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise LoginError("login_failed") from None
