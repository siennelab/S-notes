from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
from types import MethodType
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from mcp.server.auth.provider import AuthorizeError
from starlette.applications import Starlette
from starlette.responses import RedirectResponse

from src.snotes.transport.mcp.greencloud_remote_auth import (
    ALLOWED_REDIRECT_URIS,
    AUDIENCE,
    CHATGPT_REDIRECT_URI,
    GreenCloudRemoteAuthConfig,
    GreenCloudRemoteAuthError,
    LOOPBACK_REDIRECT_PATTERN,
    OAUTH_STORAGE_DIRECTORY,
    PROXY_BASE_URL,
    Auth0TokenVerifier,
    _load_json_file,
    _rfc9207_redirect_location,
    build_auth0_provider,
    load_auth_config,
)


ISSUER = "https://fictional-tenant.example.test/"
SUBJECT = "auth0|fictional-owner"
FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


class StaticJwksClient:
    def __init__(self, public_key) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token: str):
        return type("SigningKey", (), {"key": self.public_key})()


class GreenCloudGaleRemoteAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-greencloud-auth-fictional-"
        )
        self.root = Path(self.temporary.name)
        self.storage = self.root / "oauth"
        self.storage.mkdir(mode=0o700)
        os.chmod(self.storage, 0o700)
        self.private_key = rsa.generate_private_key(
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
        self.config = GreenCloudRemoteAuthConfig(
            issuer_url=ISSUER,
            jwks_url=f"{ISSUER}.well-known/jwks.json",
            auth0_config_url=f"{ISSUER}.well-known/openid-configuration",
            auth0_client_id="fictional-client",
            auth0_client_secret="fictional-secret",
            proxy_base_url=PROXY_BASE_URL,
            audience=AUDIENCE,
            oauth_storage_directory=self.storage,
            allowed_client_redirect_uris=(
                CHATGPT_REDIRECT_URI,
                LOOPBACK_REDIRECT_PATTERN,
            ),
            allowed_subjects=frozenset({SUBJECT}),
            storage_encryption_key=FERNET_KEY,
            jwt_signing_key="fictional-signing-key-with-stable-length-0001",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _provider(self):
        # The production path is fixed; this replacement is only the fictional
        # storage location passed to the explicit construction seam.
        import src.snotes.transport.mcp.greencloud_remote_auth as auth

        original = auth.OAUTH_STORAGE_DIRECTORY
        auth.OAUTH_STORAGE_DIRECTORY = self.storage
        try:
            return build_auth0_provider(
                config=self.config,
                expected_owner_uid=os.geteuid(),
                jwks_client=self.jwks_client,
                oidc_configuration=self.oidc,
            )
        finally:
            auth.OAUTH_STORAGE_DIRECTORY = original

    def _token(self, **overrides) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": SUBJECT,
            "azp": "fictional-client",
            "scope": "gale.read openid",
            "exp": int(time.time()) + 300,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def test_verifier_requires_exact_claims_subject_and_scope(self) -> None:
        verifier = Auth0TokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url=f"{ISSUER}.well-known/jwks.json",
            allowed_subjects={SUBJECT},
            jwks_client=self.jwks_client,
        )

        accepted = asyncio.run(verifier.verify_token(self._token()))
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.resource, AUDIENCE)
        for rejected in (
            self._token(sub="auth0|someone-else"),
            self._token(scope="openid"),
            self._token(scope="gale.read admin"),
            self._token(aud="https://wrong.example.test/mcp"),
            self._token(iss="https://wrong.example.test/"),
        ):
            self.assertIsNone(asyncio.run(verifier.verify_token(rejected)))

    def test_secret_file_rejects_symlink_permissions_owner_and_oversize(self) -> None:
        secret = self.root / "remote-oauth.json"
        secret.write_text(json.dumps({"fictional": True}), encoding="utf-8")
        os.chmod(secret, 0o600)
        loaded = _load_json_file(
            secret,
            maximum_bytes=1024,
            error_prefix="credential",
            allowed_modes={0o400, 0o600},
            allowed_owner_uids={os.geteuid()},
        )
        self.assertEqual(loaded, {"fictional": True})

        os.chmod(secret, 0o644)
        with self.assertRaisesRegex(
            GreenCloudRemoteAuthError,
            "credential_permissions_unsafe",
        ):
            _load_json_file(
                secret,
                maximum_bytes=1024,
                error_prefix="credential",
                allowed_modes={0o400, 0o600},
                allowed_owner_uids={os.geteuid()},
            )
        os.chmod(secret, 0o600)
        with self.assertRaisesRegex(
            GreenCloudRemoteAuthError,
            "credential_owner_unsafe",
        ):
            _load_json_file(
                secret,
                maximum_bytes=1024,
                error_prefix="credential",
                allowed_modes={0o400, 0o600},
                allowed_owner_uids={os.geteuid() + 1},
            )

        link = self.root / "linked.json"
        link.symlink_to(secret)
        with self.assertRaisesRegex(
            GreenCloudRemoteAuthError,
            "credential_symlink_rejected",
        ):
            _load_json_file(
                link,
                maximum_bytes=1024,
                error_prefix="credential",
                allowed_modes={0o400, 0o600},
                allowed_owner_uids={os.geteuid()},
            )

        secret.write_bytes(b"{" + b"x" * 1024 + b"}")
        with self.assertRaisesRegex(
            GreenCloudRemoteAuthError,
            "credential_too_large",
        ):
            _load_json_file(
                secret,
                maximum_bytes=1024,
                error_prefix="credential",
                allowed_modes={0o400, 0o600},
                allowed_owner_uids={os.geteuid()},
            )

    def test_loader_pins_root_config_and_systemd_credential_permissions(self) -> None:
        import src.snotes.transport.mcp.greencloud_remote_auth as auth

        public = {
            "issuer_url": ISSUER,
            "jwks_url": f"{ISSUER}.well-known/jwks.json",
            "auth0_config_url": f"{ISSUER}.well-known/openid-configuration",
            "proxy_base_url": PROXY_BASE_URL,
            "audience": AUDIENCE,
            "oauth_storage_directory": str(self.storage),
            "allowed_client_redirect_uris": [
                CHATGPT_REDIRECT_URI,
                LOOPBACK_REDIRECT_PATTERN,
            ],
        }
        secret = {
            "auth0_client_id": "fictional-client",
            "auth0_client_secret": "fictional-secret",
            "allowed_subjects": [SUBJECT],
            "storage_encryption_key": FERNET_KEY,
            "jwt_signing_key": "fictional-signing-key-with-stable-length-0001",
        }
        with (
            patch.object(auth, "OAUTH_STORAGE_DIRECTORY", self.storage),
            patch.object(
                auth,
                "_load_json_file",
                side_effect=[public, secret],
            ) as loader,
            patch.dict(
                os.environ,
                {"CREDENTIALS_DIRECTORY": str(self.root)},
            ),
        ):
            loaded = load_auth_config(expected_owner_uid=4242)
        self.assertEqual(loaded.allowed_subjects, frozenset({SUBJECT}))
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(
            loader.call_args_list[0].kwargs["allowed_modes"],
            {0o600, 0o644},
        )
        self.assertEqual(loader.call_args_list[0].kwargs["allowed_owner_uids"], {0})
        self.assertEqual(
            loader.call_args_list[1].kwargs["allowed_modes"],
            {0o400, 0o440, 0o600},
        )
        self.assertEqual(
            loader.call_args_list[1].kwargs["allowed_owner_uids"],
            {0, 4242},
        )

    def test_systemd_0440_credential_requires_root_ownership_and_group(self) -> None:
        secret = self.root / "systemd-credential.json"
        secret.write_text('{"fictional":true}', encoding="utf-8")
        secret.chmod(0o440)
        metadata = secret.lstat()
        root_metadata = list(metadata)
        root_metadata[4:6] = [0, 0]
        with patch.object(Path, "lstat", return_value=os.stat_result(root_metadata)):
            self.assertEqual(
                _load_json_file(secret, maximum_bytes=1024,
                    error_prefix="credential", allowed_modes={0o440},
                    allowed_owner_uids={0}),
                {"fictional": True},
            )
        root_metadata[5] = 4242
        with patch.object(Path, "lstat", return_value=os.stat_result(root_metadata)):
            with self.assertRaisesRegex(GreenCloudRemoteAuthError,
                "credential_permissions_unsafe"):
                _load_json_file(secret, maximum_bytes=1024,
                    error_prefix="credential", allowed_modes={0o440},
                    allowed_owner_uids={0})

    def test_metadata_advertises_rfc9207_support_and_exact_issuer(self) -> None:
        provider = self._provider()
        app = Starlette(routes=provider.get_routes("/mcp"))

        async def scenario() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PROXY_BASE_URL,
            ) as client:
                response = await client.get(
                    "/.well-known/oauth-authorization-server"
                )
            self.assertEqual(response.status_code, 200)
            metadata = response.json()
            self.assertEqual(metadata["issuer"], f"{PROXY_BASE_URL}/")
            self.assertIs(
                metadata["authorization_response_iss_parameter_supported"],
                True,
            )

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PROXY_BASE_URL,
            ) as client:
                resource_response = await client.get(
                    "/.well-known/oauth-protected-resource/mcp"
                )
            self.assertEqual(resource_response.status_code, 200)
            self.assertEqual(
                resource_response.json()["authorization_servers"],
                [metadata["issuer"]],
            )

        asyncio.run(scenario())

    def test_downstream_success_and_error_redirects_receive_issuer(self) -> None:
        provider = self._provider()

        async def fictional_callback(_provider, request):
            if request.query_params.get("result") == "success":
                return RedirectResponse(
                    f"{CHATGPT_REDIRECT_URI}?code=fictional&state=state-1"
                )
            return RedirectResponse(
                "http://127.0.0.1:43123/callback"
                "?error=access_denied&state=state-2"
            )

        provider._handle_idp_callback = MethodType(
            fictional_callback,
            provider,
        )
        app = Starlette(routes=provider.get_routes("/mcp"))

        async def scenario() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PROXY_BASE_URL,
                follow_redirects=False,
            ) as client:
                success = await client.get("/auth/callback?result=success")
                error = await client.get("/auth/callback?result=error")
            for response in (success, error):
                query = parse_qs(urlsplit(response.headers["location"]).query)
                self.assertEqual(query["iss"], [f"{PROXY_BASE_URL}/"])

        asyncio.run(scenario())

        upstream = RedirectResponse(
            f"{ISSUER}authorize?error=fictional-upstream-value"
        )
        original = upstream.headers["location"]
        updated = _rfc9207_redirect_location(
            original,
            provider,
            f"{PROXY_BASE_URL}/",
        )
        self.assertEqual(updated, original)

    def test_redirect_allowlist_is_exact_and_rejects_external_wildcards(self) -> None:
        provider = self._provider()
        self.assertEqual(
            frozenset(provider._allowed_client_redirect_uris),
            ALLOWED_REDIRECT_URIS,
        )
        self.assertTrue(provider._validate_client_redirect_uri(CHATGPT_REDIRECT_URI))
        self.assertTrue(
            provider._validate_client_redirect_uri(
                "http://127.0.0.1:49152/callback"
            )
        )
        self.assertFalse(
            provider._validate_client_redirect_uri(
                "https://chatgpt.com/connector/oauth/unapproved"
            )
        )
        self.assertFalse(
            provider._validate_client_redirect_uri(
                "https://attacker.example.test/callback"
            )
        )

    def test_wrong_resource_fails_closed_before_transaction(self) -> None:
        provider = self._provider()
        provider.get_routes("/mcp")

        class Client:
            client_id = "fictional-downstream-client"

        class Params:
            resource = "https://wrong.example.test/mcp"
            redirect_uri = CHATGPT_REDIRECT_URI
            state = "fictional-state"
            scopes = ["gale.read"]
            code_challenge = "fictional-challenge"
            code_challenge_method = "S256"

        try:
            asyncio.run(provider.authorize(Client(), Params()))
        except AuthorizeError as error:
            self.assertEqual(error.error, "invalid_request")
            self.assertEqual(error.error_description, "resource_mismatch")
        else:
            self.fail("wrong resource was accepted")

    def test_config_rejects_wrong_fixed_audience_and_broad_redirect(self) -> None:
        import src.snotes.transport.mcp.greencloud_remote_auth as auth

        original = auth.OAUTH_STORAGE_DIRECTORY
        auth.OAUTH_STORAGE_DIRECTORY = self.storage
        try:
            for config, code in (
                (
                    replace(
                        self.config,
                        audience="https://wrong.example.test/mcp",
                    ),
                    "audience_mismatch",
                ),
                (
                    replace(
                        self.config,
                        allowed_client_redirect_uris=(
                            "https://chatgpt.com/*",
                            LOOPBACK_REDIRECT_PATTERN,
                        ),
                    ),
                    "allowed_client_redirect_uris_invalid",
                ),
            ):
                with self.assertRaisesRegex(GreenCloudRemoteAuthError, code):
                    build_auth0_provider(
                        config=config,
                        expected_owner_uid=os.geteuid(),
                        jwks_client=self.jwks_client,
                        oidc_configuration=self.oidc,
                    )
        finally:
            auth.OAUTH_STORAGE_DIRECTORY = original


if __name__ == "__main__":
    unittest.main()
