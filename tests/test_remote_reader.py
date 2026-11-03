"""Reader boundary checks use fictional mirrors, identities and signing keys only."""
from __future__ import annotations

from dataclasses import replace
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from starlette.testclient import TestClient

from src.snotes.domain.actor_mirror import ActorMirrorRecord
from src.snotes.storage.actor_mirror import EMPTY_METADATA_CATALOG, write_actor_mirror
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.reader_auth import (
    Auth0OIDC, LoginError, ORIGIN, ReaderLogin, ReaderSettings, SESSION_COOKIE,
    SESSION_TTL, TRANSACTION_COOKIE, TRANSACTION_TTL,
)
from src.snotes.transport.reader_web import SECURITY_HEADERS, create_reader_app


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        public.update(kid="fictional-key", alg="RS256", use="sig")
        cls.jwks = {"keys": [public]}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snotes-reader-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "current.db"
        self.settings = ReaderSettings("https://fictional.auth0.test/", "fictional-client",
                                       "fictional-secret", "fictional-s")
        self.now = [1000.0]
        self.login = ReaderLogin(self.settings, clock=lambda: self.now[0])
        self.oidc = Auth0OIDC(self.settings, transport=httpx.MockTransport(self.auth_server))
        self.auth_claim_overrides = {}
        self.requests = []
        self.full = ActorMirrorRecord(1, "Fictional 笔记 <title>", "中文子串 可检索正文", "# 标题\n\n**加粗**\n\n- 列表",
            "Fixture / Leaf", ("tag <x>",), "2026-11-01T00:00:00Z", "v1", "full", 0, "full", ("s",), "Fixture", "Leaf")
        self.redacted = replace(self.full, source_note_id=2, title="Fictional redacted", safe_markdown=None,
            safe_text="第一段 [MASKED]\n\n第二段 <script>never</script> **literal**", visibility="redacted", ai_visibility="redacted", redaction_count=1)
        self.records = [self.full, self.redacted]
        self.publish()
        self.reader = SQLiteCloudMirrorReader(self.path)
        self.client = TestClient(create_reader_app(self.settings, reader=self.reader, login=self.login, oidc=self.oidc), base_url=ORIGIN)
        self.addCleanup(self.client.close)

    def publish(self):
        catalog = {**EMPTY_METADATA_CATALOG, "directories": [
            {"directory_id": 7, "name": "Fixture", "parent_id": None, "main_category": "Fixture", "sub_category": "", "path": ["Fixture"]},
            {"directory_id": 8, "name": "Leaf", "parent_id": 7, "main_category": "Fixture", "sub_category": "Leaf", "path": ["Fixture", "Leaf"]},
        ]}
        write_actor_mirror(tuple(self.records), self.path, actor="s", source_schema="snotes-markdown-v1", metadata_catalog=catalog,
                           generated_at="2026-11-01T00:00:00Z")

    def token(self, nonce="fictional-nonce", **changes):
        claims = {"iss": self.settings.issuer, "aud": self.settings.client_id, "sub": "fictional-s",
                  "iat": int(time.time()) - 1, "exp": int(time.time()) + 60, "nonce": nonce}
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "fictional-key"})

    def auth_server(self, request):
        self.requests.append(request)
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json=self.jwks)
        if request.url.path == "/oauth/token":
            body = parse_qs(request.content.decode())
            self.assertEqual(body["code"], ["fictional-code"])
            self.assertEqual(body["client_secret"], ["fictional-secret"])
            self.assertEqual(body["redirect_uri"], [ORIGIN + "/auth/callback"])
            self.assertEqual(body["code_verifier"], [self.transaction.verifier])
            return httpx.Response(200, json={"id_token": self.token(self.transaction.nonce, **self.auth_claim_overrides)})
        raise AssertionError("unexpected OIDC network call")

    def sign_in(self):
        response = self.client.get("/login", follow_redirects=False)
        params = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(params["scope"], ["openid"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertNotIn("audience", params)
        self.transaction = self.login.pending[params["state"][0]]
        self.state = params["state"][0]
        return self.client.get("/auth/callback", params={"state": self.state, "code": "fictional-code"}, follow_redirects=False)

    def csrf(self):
        return self.login.session(self.client.cookies.get(SESSION_COOKIE)).csrf

    def test_unauthenticated_content_and_unknown_routes(self):
        for method, path in (("GET", "/"), ("GET", "/note/1"), ("GET", "/directory/8"), ("POST", "/search")):
            response = self.client.request(method, path, follow_redirects=True)
            self.assertNotIn("Fictional", response.text)
            self.assertNotIn("可检索正文", response.text)
        self.assertEqual(self.client.get("/api").status_code, 404)
        self.assertEqual(self.client.get("/healthz").text, "ok")

    def test_full_oidc_flow_cookie_rotation_and_logout(self):
        self.assertEqual(self.sign_in().status_code, 303)
        session_id = self.client.cookies.get(SESSION_COOKIE)
        self.assertTrue(session_id)
        self.assertFalse(self.login.pending)
        home = self.client.get("/")
        self.assertIn("内容快照生成于：", home.text)
        self.assertNotIn("最后同步", home.text)
        self.assertIn("/directory/8", home.text)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.post("/logout", data={"csrf": "wrong"}).status_code, 400)
        self.assertEqual(self.client.post("/logout", data={"csrf": self.csrf()}, follow_redirects=False).status_code, 303)
        self.assertIsNone(self.login.session(session_id))

    def test_wrong_subject_rejected_without_session(self):
        self.auth_claim_overrides = {"sub": "fictional-other"}
        response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.login.sessions)
        self.assertNotIn("Fictional", response.text)

    def test_oidc_claims_signature_algorithm_and_nonce_fail_closed(self):
        self.oidc.verify_id_token(self.token(), "fictional-nonce", self.jwks)
        for changes in ({"iss": "https://wrong.test/"}, {"aud": "wrong"}, {"aud": ["fictional-client", "other"]},
                        {"exp": int(time.time()) - 2}, {"nonce": "wrong"}, {"sub": "wrong"}, {"azp": "wrong"},
                        {"iat": int(time.time()) + 3600}, {"sub": None}, {"exp": None}):
            with self.subTest(changes=changes), self.assertRaises(LoginError):
                self.oidc.verify_id_token(self.token(**changes), "fictional-nonce", self.jwks)
        for missing in ("iss", "aud", "exp", "iat", "sub", "nonce"):
            claims = jwt.decode(self.token(), options={"verify_signature": False})
            claims.pop(missing)
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "fictional-key"})
            with self.subTest(missing=missing), self.assertRaises(LoginError):
                self.oidc.verify_id_token(token, "fictional-nonce", self.jwks)
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(claims, other, algorithm="RS256", headers={"kid": "fictional-key"})
        symmetric = jwt.encode(claims, "fictional-signing-secret-32-characters", algorithm="HS256", headers={"kid": "fictional-key"})
        for token in (forged, symmetric, "invalid"):
            with self.assertRaises(LoginError):
                self.oidc.verify_id_token(token, "fictional-nonce", self.jwks)

    def test_state_browser_binding_expiry_replay(self):
        self.client.get("/login", follow_redirects=False)
        state = next(iter(self.login.pending))
        self.client.cookies.clear()
        failed = self.client.get("/auth/callback", params={"state": state, "code": "fictional-code"})
        self.assertEqual(failed.status_code, 403)
        self.assertFalse(self.requests)
        self.assertEqual(self.sign_in().status_code, 303)
        replay = self.client.get("/auth/callback", params={"state": self.state, "code": "fictional-code"})
        self.assertEqual(replay.status_code, 403)
        self.client.get("/login", follow_redirects=False)
        state = next(iter(self.login.pending))
        self.now[0] += TRANSACTION_TTL + 1
        self.assertEqual(self.client.get("/auth/callback", params={"state": state, "code": "fictional-code"}).status_code, 403)

    def test_secure_cookie_and_session_absolute_expiry(self):
        response = self.sign_in()
        cookie = response.headers.get_list("set-cookie")[0]
        for expected in ("__Host-", "Secure", "HttpOnly", "SameSite=lax", "Path=/"):
            self.assertIn(expected, cookie)
        self.assertNotIn("Domain=", cookie)
        self.now[0] += SESSION_TTL + 1
        self.assertNotIn("Fictional", self.client.get("/").text)
        self.assertFalse(self.login.sessions)
        restarted = ReaderLogin(self.settings)
        self.assertIsNone(restarted.session(self.client.cookies.get(SESSION_COOKIE)))

    def test_chinese_post_search_and_directory(self):
        self.sign_in()
        response = self.client.post("/search", data={"csrf": self.csrf(), "q": "子串"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("/note/1", response.text)
        self.assertNotIn("?", str(response.url))
        self.assertEqual(self.client.get("/search?q=x").status_code, 405)
        self.assertIn("/note/2", self.client.get("/directory/8").text)
        self.assertEqual(self.client.get("/directory/999").status_code, 404)
        self.assertEqual(self.client.get("/note/999").status_code, 404)
        self.assertEqual(self.client.post("/search", data={"q": "子串"}).status_code, 400)
        self.assertEqual(self.client.post("/search", data={"csrf": self.csrf(), "q": "x"}, headers={"origin": "https://evil.test"}).status_code, 400)

    def test_render_read_only_and_revocation_on_next_request(self):
        self.sign_in()
        full = self.client.get("/note/1")
        self.assertIn("<strong>加粗</strong>", full.text)
        self.assertIn("&lt;title&gt;", full.text)
        redacted = self.client.get("/note/2")
        self.assertIn("第一段 [MASKED]\n\n第二段 &lt;script&gt;", redacted.text)
        self.assertNotIn("<strong>literal", redacted.text)
        self.assertEqual(self.client.post("/note/1", data={"body": "edit"}).status_code, 405)
        self.records = [self.redacted]
        self.publish()
        self.assertEqual(self.client.get("/note/1").status_code, 404)
        self.assertNotIn("/note/1", self.client.post("/search", data={"csrf": self.csrf(), "q": "子串"}).text)

    def test_reader_minimal_methods_read_fresh_snapshots(self):
        self.assertEqual(self.reader.snapshot_info()["note_count"], 2)
        self.assertEqual(len(self.reader.list_recent(1)), 1)
        self.assertEqual(len(self.reader.list_by_location("Fixture", "Leaf")), 2)
        self.assertEqual(self.reader.list_by_location("Fixture' OR 1=1 --", "Leaf"), [])
        self.assertNotIn("safe_markdown", self.reader.get(1))
        self.assertIsNone(self.reader.get(2, include_markdown=True)["safe_markdown"])
        os.unlink(self.path)
        # No cached connection to an unlinked source remains usable.
        with self.assertRaises(Exception):
            self.reader.list_recent()

    def test_uniform_headers_and_no_external_assets(self):
        for path in ("/", "/reader.css", "/healthz", "/missing"):
            response = self.client.get(path)
            for name, value in SECURITY_HEADERS.items():
                self.assertEqual(response.headers[name], value)
        response = self.client.get("/", headers={"host": "evil.test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("<script", self.client.get("/").text)


if __name__ == "__main__":
    unittest.main()
