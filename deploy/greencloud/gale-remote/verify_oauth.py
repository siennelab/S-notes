"""Root-only live OAuth/HTTP gate; never calls a real data tool.

Forward local 8878 to GreenCloud 127.0.0.1:8769 while this runs. Open the
private authorization URL in a browser. Refresh tokens and temporary client
registrations are removed after the gate; access tokens expire normally.
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


BASE = "https://snotes-gale.ke-den.de"
CALLBACK = "http://127.0.0.1:8878/callback"
WORK = Path("/root/greencloud-secrets/gale-remote-verification")
CLIENT_NAME = "GreenCloud isolated OAuth verification"


async def cleanup_verification_clients() -> dict:
    """Remove only owned registration/refresh metadata; access expires by TTL.

    FileTreeStore has no enumeration API. Read its managed entries through the
    configured serialization/encryption adapters, then use SDK delete methods.
    No SDK/library file, access-token mapping or other client is modified.
    """
    from src.snotes.transport.mcp.greencloud_remote_auth import (
        _parse_auth_config, build_auth0_provider,
    )
    config = _parse_auth_config(
        json.loads(Path("/srv/snotes/gale-remote/config.json").read_text()),
        json.loads(Path("/root/greencloud-secrets/gale-remote-oauth.json").read_text()),
    )
    provider = build_auth0_provider(config=config, expected_owner_uid=2007)
    storage = provider._client_storage
    root = config.oauth_storage_directory.resolve()

    def entries(collection):
        for info_file in root.glob("*-info.json"):
            info = json.loads(info_file.read_text())
            if info.get("collection") != collection:
                continue
            directory = Path(info["directory"]).resolve()
            assert directory.is_relative_to(root), "cleanup_path_outside_oauth"
            for path in directory.glob("*.json"):
                assert not path.is_symlink(), "cleanup_symlink_rejected"
                entry = storage.key_value._serialization_adapter.load_dict(
                    data=json.loads(path.read_text()))
                value = storage._decrypt_value(dict(entry.value))
                if value is not None:
                    yield path.stem, value

    owned = set()
    for _key, registered in entries("mcp-oauth-proxy-clients"):
        if (registered.get("client_name") == CLIENT_NAME
                and registered.get("redirect_uris") == [CALLBACK]):
            owned.add(registered["client_id"])
    removed_refresh = 0
    for key, value in entries("mcp-refresh-tokens"):
        if value.get("client_id") in owned:
            assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
            removed_refresh += bool(await provider._refresh_token_store.delete(key=key))
    for key in owned:
        await provider._client_store.delete(key=key)
    return {"temporary_registrations_removed": len(owned),
        "temporary_refresh_metadata_removed": removed_refresh,
        "access_token_policy": "normal_expiry_no_blacklist"}


def main() -> None:
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    os.umask(0o077)
    WORK.mkdir(mode=0o700, exist_ok=True)
    if sys.argv[1:] == ["--cleanup-only"]:
        print(json.dumps(asyncio.run(cleanup_verification_clients())), flush=True)
        return
    restart_only = sys.argv[1:] == ["--restart-only"]
    assert not sys.argv[1:] or restart_only, "arguments_invalid"
    asyncio.run(cleanup_verification_clients())
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    client = httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
    registration = client.post(BASE + "/register", json={
        "client_name": CLIENT_NAME,
        "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "scope": "gale.read",
    })
    assert registration.status_code == 201, "registration_failed"
    client_id = registration.json()["client_id"]
    authorize = BASE + "/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": CALLBACK,
        "response_type": "code", "scope": "gale.read", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": BASE + "/mcp",
    })
    (WORK / "authorize-url").write_text(authorize, encoding="utf-8")
    tokens: dict = {}
    issued_access: list[str] = []
    cleaned = False
    done = threading.Event()
    callback_error: list[str] = []

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass  # Request targets contain authorization codes.

        def do_GET(self) -> None:
            path = urlparse(self.path)
            if path.path == "/done":
                body = b"GreenCloud OAuth callback received. Verification continues privately on the server."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                query = parse_qs(path.query)
                assert path.path == "/callback", "callback_path_invalid"
                assert query.get("state") == [state], "callback_state_invalid"
                assert query.get("iss") == [BASE + "/"], "callback_issuer_invalid"
                assert "error" not in query, "upstream_authorization_failed"
                response = client.post(BASE + "/token", data={
                    "grant_type": "authorization_code", "code": query["code"][0],
                    "redirect_uri": CALLBACK, "client_id": client_id,
                    "code_verifier": verifier,
                })
                assert response.status_code == 200, "token_exchange_failed"
                tokens.update(response.json())
                assert tokens.get("access_token"), "access_token_missing"
            except Exception as error:
                # Do not print URLs, codes, provider bodies or exception text.
                callback_error.append(type(error).__name__)
            finally:
                self.send_response(303)
                self.send_header("Location", "/done")
                self.end_headers()
                done.set()

    server = HTTPServer(("127.0.0.1", 8769), Callback)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("LIVE_OAUTH_BROWSER_STEP=WAITING", flush=True)
    report: dict = {"resource": BASE + "/mcp", "data_tools_called": False}
    try:
        assert done.wait(900), "browser_authorization_timeout"
        assert not callback_error, "callback_failed"
        token = tokens["access_token"]
        issued_access.append(token)
        headers = {"Accept": "application/json, text/event-stream"}

        def rpc(base: str, method: str, params: dict, bearer: str | None):
            request_headers = dict(headers)
            if bearer is not None:
                request_headers["Authorization"] = "Bearer " + bearer
            return client.post(base + "/mcp", headers=request_headers, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            })

        init_params = {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "isolated-live-gate", "version": "1"}}
        contracts = []
        endpoints = [("public", BASE)] if restart_only else [
            ("localhost", "http://127.0.0.1:8768"), ("public", BASE)]
        for label, base in endpoints:
            init = rpc(base, "initialize", init_params, token)
            assert init.status_code == 200 and "result" in init.json(), "authenticated_initialize_failed"
            listed = rpc(base, "tools/list", {}, token)
            assert listed.status_code == 200, "authenticated_tools_list_failed"
            tools = listed.json()["result"]["tools"]
            assert len(tools) == 25, "tool_count_invalid"
            for tool in tools:
                security = [{"type": "oauth2", "scopes": ["gale.read"]}]
                assert tool["securitySchemes"] == tool["_meta"]["securitySchemes"] == security
            contracts.append(tools)
            if restart_only:
                report[label] = {"authenticated_initialize": "PASS", "tool_count": 25}
                continue
            # No auth must stop BEFORE a real data tool is dispatched.
            absent = rpc(base, "tools/call", {"name": "search_notes", "arguments": {"query": "fictional"}}, None)
            assert absent.status_code == 200
            assert absent.json()["result"]["isError"] is True
            assert absent.json()["result"]["_meta"]["mcp/www_authenticate"]
            wrong = rpc(base, "tools/call", {"name": "search_notes", "arguments": {"query": "fictional"}}, "fictional-invalid-credential")
            assert wrong.status_code == 401 and "www-authenticate" in wrong.headers
            wrong_discovery = rpc(base, "tools/list", {}, "fictional-invalid-credential")
            assert wrong_discovery.status_code == 401
            report[label] = {"authenticated_initialize": "PASS", "tool_count": 25,
                "security_metadata": "PASS", "anonymous_call_challenge": "PASS",
                "invalid_token_call_and_discovery": "PASS"}
        if not restart_only:
            assert contracts[0] == contracts[1], "public_local_contract_mismatch"
        schema = json.dumps(contracts[0], sort_keys=True, separators=(",", ":")).encode()
        report["contract_sha256"] = hashlib.sha256(schema).hexdigest()
        subprocess.run(["systemctl", "restart", "snotes-gale-remote-mcp.service"], check=True)
        for attempt in range(30):
            try:
                restarted = rpc(BASE, "initialize", init_params, token)
                if restarted.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if attempt == 29:
                raise RuntimeError("authenticated_restart_recovery_failed")
            time.sleep(0.5)
        report["authenticated_after_service_restart"] = "PASS"
        after_restart = rpc(BASE, "tools/list", {}, token)
        assert after_restart.status_code == 200
        assert after_restart.json()["result"]["tools"] == contracts[0]
        report["restart_authenticated_tools_list"] = 25
        (WORK / "restart-verification.partial.json").write_text(json.dumps(report, indent=2) + "\n")
        assert tokens.get("refresh_token"), "refresh_token_missing"
        if not restart_only:
            refreshed = client.post(BASE + "/token", data={"grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"], "client_id": client_id})
            assert refreshed.status_code == 200, "refresh_failed"
            tokens.update(refreshed.json())
            issued_access.append(tokens["access_token"])
            assert rpc(BASE, "ping", {}, tokens["access_token"]).status_code == 200
            report["real_auth0_code_pkce_exact_issuer_refresh"] = "PASS"
        cleanup = asyncio.run(cleanup_verification_clients())
        cleaned = True
        assert client.post(BASE + "/token", data={"grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"], "client_id": client_id}).status_code in {400, 401}
        import jwt
        report["verification_access_expiry_unix"] = max(
            jwt.decode(access, options={"verify_signature": False})["exp"] for access in issued_access)
        report["temporary_refresh_metadata_and_client_removed"] = "PASS"
        report["cleanup"] = cleanup
        # Keep useful evidence free of tokens, subjects and credentials.
        report_name = "restart-verification.json" if restart_only else "verification.json"
        (WORK / report_name).write_text(json.dumps(report, indent=2) + "\n")
        (WORK / "restart-verification.partial.json").unlink(missing_ok=True)
        print(json.dumps(report, separators=(",", ":")), flush=True)
        print("LIVE_OAUTH_AUTHENTICATED_GATE=PASS", flush=True)
    finally:
        if not cleaned:
            asyncio.run(cleanup_verification_clients())
        tokens.clear()
        issued_access.clear()
        (WORK / "authorize-url").unlink(missing_ok=True)
        # Allow the browser's clean /done request to finish before shutdown.
        time.sleep(1)
        server.shutdown()
        server.server_close()
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("LIVE_OAUTH_GATE=FAIL ERROR_TYPE=" + type(error).__name__, flush=True)
        print("SAFE_TRACE=" + ",".join(frame.name + ":" + str(frame.lineno)
            for frame in traceback.extract_tb(error.__traceback__)), flush=True)
        raise SystemExit(1)
