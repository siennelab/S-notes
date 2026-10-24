from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
import jwt

from src.snotes.transport.mcp.claude_web_remote_auth import (
    ALLOWED_REDIRECT_URIS,
    AUDIENCE,
    CLAUDE_REDIRECT_URI,
    CONFIG_PATH,
    CREDENTIAL_NAME,
    LOOPBACK_REDIRECT_PATTERN,
    NOTES_ACCESS_SCOPE,
    OAUTH_STORAGE_DIRECTORY,
    PROXY_BASE_URL,
    RUNTIME_ACCOUNT,
    RUNTIME_UID,
    ClaudeWebRemoteAuthConfig,
    ClaudeWebRemoteAuthError,
    build_auth0_provider,
    build_auth_provider,
    load_auth_config,
)
from src.snotes.transport.mcp.greencloud_remote_auth import (
    AUDIENCE as GALE_AUDIENCE,
    CREDENTIAL_NAME as GALE_CREDENTIAL_NAME,
    OAUTH_STORAGE_DIRECTORY as GALE_OAUTH_STORAGE_DIRECTORY,
    PROXY_BASE_URL as GALE_PROXY_BASE_URL,
    Auth0TokenVerifier,
)


ISSUER = "https://fictional-tenant.example.test/"
SUBJECT = "auth0|fictional-claude-owner"
FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


class StaticJwksClient:
    def __init__(self, public_key) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str):
        return type("SigningKey", (), {"key": self.public_key})()


class ClaudeWebRemoteAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-claude-web-auth-fictional-"
        )
        self.root = Path(self.temporary.name)
        self.storage = self.root / "oauth"
        self.storage.mkdir(mode=0o700)
        self.storage.chmod(0o700)
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.other_private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.jwks_client = StaticJwksClient(self.private_key.public_key())
        self.oidc = OIDCConfiguration(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}authorize",
            token_endpoint=f"{ISSUER}oauth/token",
            jwks_uri=f"{ISSUER}.well-known/jwks.json",
            response_types_supported=["code"],
            subject_types_supported=["public"],
            id_token_signing_alg_values_supported=["RS256"],
            code_challenge_methods_supported=["S256"],
        )
        self.config = ClaudeWebRemoteAuthConfig(
            issuer_url=ISSUER,
            jwks_url=f"{ISSUER}.well-known/jwks.json",
            auth0_config_url=f"{ISSUER}.well-known/openid-configuration",
            auth0_client_id="fictional-claude-client",
            auth0_client_secret="fictional-claude-secret",
            proxy_base_url=PROXY_BASE_URL,
            audience=AUDIENCE,
            oauth_storage_directory=self.storage,
            allowed_client_redirect_uris=(
                CLAUDE_REDIRECT_URI,
                LOOPBACK_REDIRECT_PATTERN,
            ),
            allowed_subjects=frozenset({SUBJECT}),
            storage_encryption_key=FERNET_KEY,
            jwt_signing_key="fictional-claude-signing-key-long-enough-0001",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _token(self, *, signing_key=None, **overrides) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": SUBJECT,
            "azp": "fictional-claude-client",
            "scope": "notes openid",
            "exp": int(time.time()) + 300,
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            signing_key or self.private_key,
            algorithm="RS256",
        )

    def _provider(self):
        import src.snotes.transport.mcp.claude_web_remote_auth as auth

        with patch.object(auth, "OAUTH_STORAGE_DIRECTORY", self.storage):
            return build_auth0_provider(
                config=self.config,
                expected_owner_uid=os.geteuid(),
                jwks_client=self.jwks_client,
                oidc_configuration=self.oidc,
            )

    def test_notes_verifier_rejects_wrong_claims_scope_and_signing_key(self) -> None:
        verifier = Auth0TokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url=f"{ISSUER}.well-known/jwks.json",
            allowed_subjects={SUBJECT},
            access_scope=NOTES_ACCESS_SCOPE,
            jwks_client=self.jwks_client,
        )

        accepted = asyncio.run(verifier.verify_token(self._token()))
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.resource, AUDIENCE)
        self.assertEqual(accepted.scopes, ["notes", "openid"])
        for rejected in (
            self._token(iss="https://wrong.example.test/"),
            self._token(aud=GALE_AUDIENCE),
            self._token(sub="auth0|fictional-gale-owner"),
            self._token(scope="gale.read openid"),
            self._token(scope="notes openid admin"),
            self._token(signing_key=self.other_private_key),
        ):
            self.assertIsNone(asyncio.run(verifier.verify_token(rejected)))

    def test_gale_verifier_default_scope_is_unchanged(self) -> None:
        verifier = Auth0TokenVerifier(
            issuer=ISSUER,
            audience=GALE_AUDIENCE,
            jwks_url=f"{ISSUER}.well-known/jwks.json",
            allowed_subjects={SUBJECT},
            jwks_client=self.jwks_client,
        )
        gale_token = self._token(
            aud=GALE_AUDIENCE,
            scope="gale.read openid",
        )
        notes_token = self._token(aud=GALE_AUDIENCE, scope="notes openid")
        self.assertIsNotNone(asyncio.run(verifier.verify_token(gale_token)))
        self.assertIsNone(asyncio.run(verifier.verify_token(notes_token)))

    def test_provider_uses_only_notes_and_claude_redirects(self) -> None:
        provider = self._provider()
        self.assertEqual(provider._access_scope, "notes")
        self.assertEqual(provider.required_scopes, ["notes"])
        self.assertEqual(
            frozenset(provider._allowed_client_redirect_uris),
            ALLOWED_REDIRECT_URIS,
        )
        self.assertTrue(provider._validate_client_redirect_uri(CLAUDE_REDIRECT_URI))
        self.assertTrue(
            provider._validate_client_redirect_uri(
                "http://127.0.0.1:43123/callback"
            )
        )
        for rejected in (
            "https://claude.com/api/mcp/auth_callback",
            "https://chatgpt.com/connector_platform_oauth_redirect",
            "https://attacker.example.test/callback",
        ):
            self.assertFalse(provider._validate_client_redirect_uri(rejected))

    def test_fixed_bindings_reject_gale_config_and_redirects(self) -> None:
        import src.snotes.transport.mcp.claude_web_remote_auth as auth

        self.assertNotEqual(PROXY_BASE_URL, GALE_PROXY_BASE_URL)
        self.assertNotEqual(AUDIENCE, GALE_AUDIENCE)
        self.assertNotEqual(OAUTH_STORAGE_DIRECTORY, GALE_OAUTH_STORAGE_DIRECTORY)
        self.assertNotEqual(CREDENTIAL_NAME, GALE_CREDENTIAL_NAME)
        with patch.object(auth, "OAUTH_STORAGE_DIRECTORY", self.storage):
            for config, code in (
                (
                    replace(self.config, proxy_base_url=GALE_PROXY_BASE_URL),
                    "proxy_base_url_mismatch",
                ),
                (
                    replace(self.config, audience=GALE_AUDIENCE),
                    "audience_mismatch",
                ),
                (
                    replace(
                        self.config,
                        allowed_client_redirect_uris=(
                            "https://chatgpt.com/connector_platform_oauth_redirect",
                            LOOPBACK_REDIRECT_PATTERN,
                        ),
                    ),
                    "allowed_client_redirect_uris_invalid",
                ),
            ):
                with self.assertRaisesRegex(ClaudeWebRemoteAuthError, code):
                    build_auth0_provider(
                        config=config,
                        expected_owner_uid=os.geteuid(),
                        jwks_client=self.jwks_client,
                        oidc_configuration=self.oidc,
                    )

    def test_loader_reads_only_fixed_root_config_and_claude_credential(self) -> None:
        import src.snotes.transport.mcp.claude_web_remote_auth as auth

        public = {
            "issuer_url": ISSUER,
            "jwks_url": f"{ISSUER}.well-known/jwks.json",
            "auth0_config_url": f"{ISSUER}.well-known/openid-configuration",
            "proxy_base_url": PROXY_BASE_URL,
            "audience": AUDIENCE,
            "oauth_storage_directory": str(self.storage),
            "allowed_client_redirect_uris": [
                CLAUDE_REDIRECT_URI,
                LOOPBACK_REDIRECT_PATTERN,
            ],
        }
        secret = {
            "auth0_client_id": "fictional-claude-client",
            "auth0_client_secret": "fictional-claude-secret",
            "allowed_subjects": [SUBJECT],
            "storage_encryption_key": FERNET_KEY,
            "jwt_signing_key": "fictional-claude-signing-key-long-enough-0001",
        }
        with (
            patch.object(auth, "OAUTH_STORAGE_DIRECTORY", self.storage),
            patch.object(
                auth,
                "_load_json_file",
                side_effect=[public, secret],
            ) as loader,
            patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.root)}),
        ):
            loaded = load_auth_config(expected_owner_uid=RUNTIME_UID)

        self.assertEqual(loaded.auth0_client_id, "fictional-claude-client")
        self.assertEqual(loader.call_args_list[0].args[0], CONFIG_PATH)
        self.assertEqual(loader.call_args_list[0].kwargs["allowed_owner_uids"], {0})
        self.assertEqual(
            loader.call_args_list[1].args[0],
            self.root / "claude-web-oauth.json",
        )
        self.assertEqual(
            loader.call_args_list[1].kwargs["allowed_owner_uids"],
            {0, RUNTIME_UID},
        )

    def test_entrypoint_pins_runtime_name_uid_and_effective_user(self) -> None:
        account = type("Account", (), {"pw_uid": RUNTIME_UID})()
        with (
            patch(
                "src.snotes.transport.mcp.claude_web_remote_auth.pwd.getpwnam",
                return_value=account,
            ) as getpwnam,
            patch(
                "src.snotes.transport.mcp.claude_web_remote_auth.os.geteuid",
                return_value=RUNTIME_UID,
            ),
            patch(
                "src.snotes.transport.mcp.claude_web_remote_auth.load_auth_config",
                return_value=self.config,
            ),
            patch(
                "src.snotes.transport.mcp.claude_web_remote_auth.build_auth0_provider",
                return_value="fictional-provider",
            ) as builder,
        ):
            self.assertEqual(build_auth_provider(), "fictional-provider")
        getpwnam.assert_called_once_with(RUNTIME_ACCOUNT)
        builder.assert_called_once_with(
            config=self.config,
            expected_owner_uid=RUNTIME_UID,
        )

        wrong_uid_account = type("Account", (), {"pw_uid": RUNTIME_UID + 1})()
        with patch(
            "src.snotes.transport.mcp.claude_web_remote_auth.pwd.getpwnam",
            return_value=wrong_uid_account,
        ):
            with self.assertRaisesRegex(
                ClaudeWebRemoteAuthError,
                "runtime_account_uid_mismatch",
            ):
                build_auth_provider()


if __name__ == "__main__":
    unittest.main()
