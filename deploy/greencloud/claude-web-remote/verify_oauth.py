"""Root-only live OAuth/Notes gate for Claude Web's independent MCP.

Forward local 8878 to GreenCloud 127.0.0.1:8769 while this runs, then open
the root-only authorize-url in a browser. This script never logs OAuth values,
note text, or subjects and never accepts a proposed note.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
import traceback
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


BASE = "https://snotes-claude.ke-den.de"
LOCAL = "http://127.0.0.1:8770"
CALLBACK = "http://127.0.0.1:8878/callback"
CALLBACK_BIND = ("127.0.0.1", 8769)
WORK = Path("/root/greencloud-secrets/claude-web-remote-verification")
CONFIG = Path("/srv/snotes/claude-web-remote/config.json")
CREDENTIAL = Path("/root/greencloud-secrets/claude-web-remote-oauth.json")
CLIENT_NAME = "Claude Web Notes isolated OAuth verification"
REQUEST_ID = "claudeweboauthverificationv1x9q7k3"
EXPECTED_TOOLS = {
    "search_notes", "get_note", "get_note_markdown", "list_directories",
    "list_tags", "list_ai_metadata_options", "create_note", "prepend_note",
    "append_note", "update_note_metadata", "edit_note", "get_operation_status",
}


def positive_note_id(value: str) -> int:
    note_id = int(value)
    if not 1 <= note_id <= 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("note id must be a positive integer")
    return note_id


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Claude Web Notes OAuth")
    parser.add_argument("--allowed-note", required=True, type=positive_note_id)
    parser.add_argument("--denied-note", required=True, type=positive_note_id)
    parser.add_argument("--submit-proposal", action="store_true")
    return parser.parse_args()


async def cleanup_verification_clients() -> dict:
    """Remove only this gate's clients and refresh metadata."""
    from src.snotes.transport.mcp.claude_web_remote_auth import (
        _parse_auth_config,
        build_auth0_provider,
    )

    config = _parse_auth_config(
        json.loads(CONFIG.read_text()),
        json.loads(CREDENTIAL.read_text()),
    )
    provider = build_auth0_provider(config=config, expected_owner_uid=2001)
    storage = provider._client_storage
    root = config.oauth_storage_directory.resolve()

    def entries(collection: str):
        for info_file in root.glob("*-info.json"):
            info = json.loads(info_file.read_text())
            if info.get("collection") != collection:
                continue
            directory = Path(info["directory"]).resolve()
            assert directory.is_relative_to(root), "cleanup_path_outside_oauth"
            for path in directory.glob("*.json"):
                assert not path.is_symlink(), "cleanup_symlink_rejected"
                entry = storage.key_value._serialization_adapter.load_dict(
                    data=json.loads(path.read_text())
                )
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
    for client_id in owned:
        await provider._client_store.delete(key=client_id)
    return {
        "temporary_registrations_removed": len(owned),
        "temporary_refresh_metadata_removed": removed_refresh,
        "access_token_policy": "normal_expiry_no_blacklist",
    }


def mcp_result_body(result: dict) -> dict:
    if "structuredContent" in result:
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            return json.loads(item["text"])
    raise AssertionError("mcp_result_body_missing")


def result_body(response: httpx.Response) -> dict:
    return mcp_result_body(response.json()["result"])


def main() -> None:
    args = arguments()
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    os.umask(0o077)
    WORK.mkdir(mode=0o700, exist_ok=True)
    WORK.chmod(0o700)
    asyncio.run(cleanup_verification_clients())

    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    client = httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
    registration = client.post(BASE + "/register", json={
        "client_name": CLIENT_NAME,
        "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": "notes",
    })
    assert registration.status_code == 201, "registration_failed"
    client_id = registration.json()["client_id"]
    authorize = BASE + "/authorize?" + urlencode({
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "scope": "notes",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": BASE + "/mcp",
    })
    authorize_path = WORK / "authorize-url"
    authorize_path.write_text(authorize, encoding="utf-8")
    authorize_path.chmod(0o600)

    tokens: dict = {}
    done = threading.Event()
    callback_error: list[str] = []

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_GET(self) -> None:
            path = urlparse(self.path)
            if path.path == "/done":
                body = b"OAuth callback received. Private verification continues."
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
                assert "error" not in query, "authorization_failed"
                response = client.post(BASE + "/token", data={
                    "grant_type": "authorization_code",
                    "code": query["code"][0],
                    "redirect_uri": CALLBACK,
                    "client_id": client_id,
                    "code_verifier": verifier,
                })
                assert response.status_code == 200, "token_exchange_failed"
                tokens.update(response.json())
                assert tokens.get("access_token"), "access_token_missing"
            except Exception as error:
                callback_error.append(type(error).__name__)
            finally:
                self.send_response(303)
                self.send_header("Location", "/done")
                self.end_headers()
                done.set()

    server = HTTPServer(CALLBACK_BIND, Callback)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("CLAUDE_WEB_OAUTH_BROWSER_STEP=WAITING", flush=True)
    cleaned = False
    report = {"allowed_note_id": args.allowed_note,
        "denied_note_id": args.denied_note}
    try:
        assert done.wait(900), "browser_authorization_timeout"
        assert not callback_error, "callback_failed"
        token = tokens["access_token"]
        common_headers = {"Accept": "application/json, text/event-stream"}

        def rpc(base: str, method: str, params: dict, bearer: str | None,
                *, suffix: str = "/mcp", extra_headers: dict | None = None,
                extra_payload: dict | None = None) -> httpx.Response:
            headers = dict(common_headers)
            if bearer is not None:
                headers["Authorization"] = "Bearer " + bearer
            if extra_headers:
                headers.update(extra_headers)
            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            if extra_payload:
                payload.update(extra_payload)
            return client.post(base + suffix, headers=headers, json=payload)

        init_params = {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "claude-web-live-gate", "version": "1"}}
        contracts = []
        for label, base in (("localhost", LOCAL), ("public_https", BASE)):
            initialized = rpc(base, "initialize", init_params, token)
            assert initialized.status_code == 200 and "result" in initialized.json()
            listed = rpc(base, "tools/list", {}, token)
            assert listed.status_code == 200, "authenticated_tools_list_failed"
            tools = listed.json()["result"]["tools"]
            assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
            assert all("securitySchemes" not in tool for tool in tools)
            assert all("reminder" not in tool["name"] for tool in tools)
            contracts.append(tools)
            absent = rpc(base, "tools/call", {"name": "get_note",
                "arguments": {"note_id": args.allowed_note}}, None)
            invalid = rpc(base, "tools/list", {}, "fictional-invalid-token")
            assert absent.status_code == invalid.status_code == 401
            assert "www-authenticate" in absent.headers
            report[label] = {"authenticated_initialize": "PASS", "tool_count": 12,
                "missing_and_invalid_auth_rejected": "PASS"}
        assert contracts[0] == contracts[1], "public_local_contract_mismatch"

        allowed = result_body(rpc(BASE, "tools/call", {"name": "get_note",
            "arguments": {"note_id": args.allowed_note}}, token))
        denied = result_body(rpc(BASE, "tools/call", {"name": "get_note",
            "arguments": {"note_id": args.denied_note}}, token))
        assert allowed["status"] == "ok" and allowed["note_id"] == args.allowed_note
        assert denied == {"status": "not_found", "note_id": args.denied_note}
        terms = [term for term in re.findall(r"\w+", allowed["title"]) if term]
        assert terms, "allowed_note_title_not_searchable"
        search = result_body(rpc(BASE, "tools/call", {"name": "search_notes",
            "arguments": {"query": " ".join(terms)[:500], "limit": 100}}, token))
        assert search["status"] == "ok"
        assert args.allowed_note in {item["note_id"] for item in search["results"]}
        markdown = result_body(rpc(BASE, "tools/call", {"name": "get_note_markdown",
            "arguments": {"note_id": args.allowed_note}}, token))
        assert markdown["status"] in {"ok", "not_found"}
        assert markdown["note_id"] == args.allowed_note
        report["notes_read_boundary"] = "PASS"

        for actor in ("gale", "common", "antigravity", "clawbot", "arbitrary"):
            attempts = (
                {"suffix": "/mcp?" + urlencode({"actor": actor})},
                {"extra_headers": {"x-snotes-actor": actor}},
                {"extra_payload": {"actor": actor}},
            )
            for attempt in attempts:
                fixed = result_body(rpc(BASE, "tools/call", {"name": "get_note",
                    "arguments": {"note_id": args.denied_note}}, token, **attempt))
                assert fixed == {"status": "not_found", "note_id": args.denied_note}
        for field in ("actor", "source_actor", "actor_id", "actor_target_id",
                "identity", "principal", "role"):
            injected = rpc(BASE, "tools/call", {"name": "get_note", "arguments": {
                "note_id": args.allowed_note, field: "gale"}}, token).json()["result"]
            assert injected.get("isError") is True, "actor_argument_not_rejected"
        reminder = rpc(BASE, "tools/call", {
            "name": "list_reminder_tables", "arguments": {}}, token).json()["result"]
        if not reminder.get("isError"):
            body = mcp_result_body(reminder)
            assert body.get("status") not in {"ok", "queued", "created", "updated"}
        report["actor_spoof_and_reminder_boundary"] = "PASS"

        if args.submit_proposal:
            proposal = result_body(rpc(BASE, "tools/call", {"name": "create_note",
                "arguments": {"request_id": REQUEST_ID,
                    "title": "FICTIONAL " + REQUEST_ID,
                    "content": "# Fictional OAuth proposal\n\nNo formal write."}}, token))
            pending_states = {"queued", "delivered", "acknowledged"}
            assert proposal["status"] == proposal["delivery_state"]
            assert proposal["status"] in pending_states
            assert proposal["note_id"] is None and proposal["receipt"] == {}
            status = result_body(rpc(BASE, "tools/call", {
                "name": "get_operation_status", "arguments": {"request_id": REQUEST_ID}}, token))
            assert status["status"] in pending_states and status["request_id"] == REQUEST_ID
            absent = result_body(rpc(BASE, "tools/call", {"name": "search_notes",
                "arguments": {"query": REQUEST_ID}}, token))
            assert absent["status"] == "ok" and absent["result_count"] == 0
            report["proposal"] = {"request_id": REQUEST_ID, "status": status["status"],
                "direct_formal_note_write": False}

        assert tokens.get("refresh_token"), "refresh_token_missing"
        refreshed = client.post(BASE + "/token", data={"grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"], "client_id": client_id})
        assert refreshed.status_code == 200, "refresh_failed"
        tokens.update(refreshed.json())
        assert rpc(BASE, "ping", {}, tokens["access_token"]).status_code == 200
        report["code_pkce_exact_issuer_refresh"] = "PASS"
        asyncio.run(cleanup_verification_clients())
        cleaned = True
        assert client.post(BASE + "/token", data={"grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"], "client_id": client_id}).status_code in {400, 401}
        report["temporary_client_and_refresh_removed"] = "PASS"
        evidence = WORK / "verification.json"
        evidence.write_text(json.dumps(report, indent=2) + "\n")
        evidence.chmod(0o600)
        print(json.dumps(report, separators=(",", ":")), flush=True)
        print("CLAUDE_WEB_OAUTH_NOTES_GATE=PASS", flush=True)
    finally:
        if not cleaned:
            asyncio.run(cleanup_verification_clients())
        tokens.clear()
        authorize_path.unlink(missing_ok=True)
        time.sleep(1)
        server.shutdown()
        server.server_close()
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("CLAUDE_WEB_OAUTH_GATE=FAIL ERROR_TYPE=" + type(error).__name__, flush=True)
        print("SAFE_TRACE=" + ",".join(frame.name + ":" + str(frame.lineno)
            for frame in traceback.extract_tb(error.__traceback__)), flush=True)
        raise SystemExit(1)
