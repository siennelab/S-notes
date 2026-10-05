"""Shared assertions for fictional-only local and deployed MCP verification."""
from __future__ import annotations

import json


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def result_body(result):
    if getattr(result, "isError", False):
        return {"status": "rejected", "reason": "mcp_request_rejected"}
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    for content in result.content:
        if content.type == "text":
            return json.loads(content.text)
    raise RuntimeError("mcp_result_missing")


async def exercise_tools(call, manifest):
    ids = manifest["ids"]
    notes = {}
    for key in ("common", "gale", "gale_authorized", "redacted"):
        note = await call("get_note", {"note_id": ids[key]})
        require(note.get("status") == "ok", "fictional_read_" + key)
        notes[key] = note
        require({"title", "main_category", "sub_category", "tags", "ai_visibility", "ai_targets",
                 "version", "updated_at", "safe_text"}.issubset(note), "fictional_metadata_" + key)
    require(notes["gale"]["ai_targets"] == ["gale"], "gale_only_metadata")
    require(notes["gale"]["main_category"] == "Fictional" and notes["gale"]["sub_category"] == "Step5",
            "fictional_category_metadata")
    for key in ("claude", "clawbot", "hidden"):
        for tool in ("get_note", "get_note_markdown"):
            response = await call(tool, {"note_id": ids[key]})
            require(response.get("status") == "not_found", "fictional_permission_" + key)
    found = await call("search_notes", {"query": "fictional"})
    require({item["note_id"] for item in found["results"]} == {ids[key] for key in notes}, "fictional_search_isolation")
    require("FICTIONAL_REDACT_ME" not in notes["redacted"]["safe_text"] and "[REDACTED]" in notes["redacted"]["safe_text"],
            "fictional_redaction")
    require((await call("get_note_markdown", {"note_id": ids["redacted"]}))["status"] == "not_found", "redacted_markdown_denied")
    require((await call("get_note_markdown", {"note_id": ids["common"]}))["status"] == "ok", "full_markdown_allowed")
    for name, field in (("list_directories", "directories"), ("list_tags", "tags"), ("list_ai_metadata_options", "ai_targets")):
        result = await call(name, {})
        require(result.get("status") == "ok" and bool(result[field]), "fictional_catalog_" + name)
    version = notes["common"]["version"]
    operations = (
        ("create_note", {"title": "FICTIONAL proposal", "content": "# FICTIONAL create", "main_category": "Fictional"}),
        ("prepend_note", {"note_id": ids["common"], "expected_version": version, "content": "FICTIONAL prefix"}),
        ("append_note", {"note_id": ids["common"], "expected_version": version, "content": "FICTIONAL suffix"}),
        ("update_note_metadata", {"note_id": ids["common"], "expected_version": version, "title": "FICTIONAL rename",
                                  "directory_id": 1, "add_tags": ["fictional-added"], "remove_tags": ["step5"]}),
        ("edit_note", {"note_id": ids["common"], "expected_version": version,
                       "edits": [{"old_markdown": "unique", "new_markdown": "changed"}]}),
    )
    for tool, payload in operations:
        request_id = "fictional-gale-" + tool
        result = await call(tool, {"request_id": request_id, **payload})
        require(result.get("status") == "queued", "fictional_proposal_" + tool)
        status = await call("get_operation_status", {"request_id": request_id})
        require(status.get("status") == "queued", "fictional_status_" + tool)
    for field, value in (("ai_visibility", "hidden"), ("ai_targets", ["owner"])):
        result = await call("update_note_metadata", {"request_id": "fictional-blocked-" + field,
                            "note_id": ids["common"], "expected_version": version, field: value})
        require(result.get("status") != "queued", "forbidden_metadata_write")
    for name, arguments in (("get_note", {"note_id": ids["common"]}),
                             ("create_note", {"request_id": "fictional-spoof", "title": "FICTIONAL", "content": "FICTIONAL"}),
                             ("list_reminder_tables", {}),
                             ("create_reminder_table", {"request_id": "fictional-spoof-reminder", "title": "FICTIONAL"})):
        result = await call(name, {**arguments, "actor": "owner"})
        require(result.get("status") == "rejected", "actor_injection_" + name)
    result = await call("delete_note", {"note_id": ids["common"]})
    require(result.get("status") not in {"queued", "ok"}, "delete_unavailable")
    result = await call("list_reminder_tables", {})
    require(result.get("status") == "ok", "fictional_reminder_list")
    table = await call("create_reminder_table", {"request_id": "fictional-gale-reminder-create", "title": "FICTIONAL Gale smoke"})
    require(table.get("status") == "created", "fictional_reminder_mutation")
    require((await call("get_reminder_table", {"table_id": table["table_id"]}))["status"] == "ok", "fictional_reminder_read")
    require((await call("set_table_ai_visible", {"table_id": table["table_id"], "ai_visible": False}))["status"] == "failed",
            "owner_tool_unavailable")
    return {"notes_read_metadata_catalog": "PASS", "proposals": "5_PASS", "operation_status": "PASS",
            "reminder_read_mutation": "PASS", "permission_negatives": "PASS"}
