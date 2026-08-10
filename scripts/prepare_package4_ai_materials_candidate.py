#!/usr/bin/env python3
"""Prepare an isolated fictional Package 4 AI-materials candidate DB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields
from src.snotes.bootstrap import build_application, resolve_rebuild_data_root
from src.snotes.storage.connection import SQLiteConnectionFactory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    arguments = parser.parse_args()
    data_root = resolve_rebuild_data_root(arguments.data_root)
    database_path = data_root / "s_notes.db"
    if database_path.exists():
        raise RuntimeError("Candidate database already exists; use a fresh isolated root")

    application, window = build_application([], data_root=data_root)

    def add_note(
        title: str,
        *,
        visibility: str,
        targets: tuple[str, ...],
        body: str,
    ) -> int:
        note = window.note_service.create_note()
        note = window.note_service.save_note(
            note.id,
            title=title,
            content_html=f"<p>{body}</p>",
            location=note.location,
            expected_revision=note.revision,
        )
        window.tag_service.set_note_tags(note.id, ("package4", "fictional"))
        window.ai_access_metadata_service.set_note_visibility(note.id, visibility)
        if targets:
            window.ai_access_metadata_service.set_note_targets(note.id, targets)
        return note.id

    fixtures = {
        "gale_only": add_note(
            "AI资料 · Gale only · Alpha",
            visibility="full",
            targets=("gale",),
            body="FICTIONAL_GALE_ONLY searchable alpha",
        ),
        "ke_only": add_note(
            "AI资料 · Ke only · Beta",
            visibility="full",
            targets=("ke-den",),
            body="FICTIONAL_KE_ONLY searchable beta",
        ),
        "common": add_note(
            "AI资料 · 通用 · Common",
            visibility="full",
            targets=("common",),
            body="FICTIONAL_COMMON searchable common",
        ),
        "gale_claude_code": add_note(
            "AI资料 · Gale + Claude Code · Shared",
            visibility="full",
            targets=("gale", "claude-code"),
            body="FICTIONAL_SHARED searchable shared",
        ),
        "hidden_gale": add_note(
            "不应出现 · hidden + Gale",
            visibility="hidden",
            targets=("gale",),
            body="FICTIONAL_HIDDEN_GALE",
        ),
        "redacted_clawbot": add_note(
            "AI资料 · redacted + Clawbot",
            visibility="redacted",
            targets=("clawbot",),
            body="FICTIONAL_REDACTED_CLAWBOT",
        ),
        "legacy_codex": add_note(
            "AI资料 · legacy Codex（仅全部可见）",
            visibility="full",
            targets=("codex",),
            body="FICTIONAL_LEGACY_CODEX",
        ),
        "ordinary_hidden": add_note(
            "普通无 AI 资格笔记",
            visibility="hidden",
            targets=(),
            body="FICTIONAL_ORDINARY_HIDDEN",
        ),
    }

    connections = SQLiteConnectionFactory(database_path)
    receipts = (
        (201, "PENDING · 接受为 Gale AI资料", "接受后应进入 AI资料 Gale"),
        (202, "PENDING · 丢弃我", "丢弃后不创建正式笔记"),
        (203, "PROCESSED · accepted + linked（保护）", "查账保护样本"),
        (204, "PROCESSED · discarded（允许删除）", "可删除样本"),
    )
    with connections.write() as connection:
        for receipt_id, title, body in receipts:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, ai_targets, body, created_at, updated_at
                ) VALUES (
                    ?, 'FICTIONAL_AI_MATERIALS_RAW', 'parsed', 'pending',
                    'Package 4 Smart View Fixture', '新增', ?, '项目',
                    'AI资料验收', '无', 'package4, fictional', 'full', 'gale',
                    ?, '2026-08-10T09:00:00', '2026-08-10T10:00:00'
                )
                """,
                (receipt_id, title, body),
            )

    protected = window.receipt_inbox_service.accept_receipt(
        203,
        AiReceiptAcceptFields(
            title="FICTIONAL · Smart View 查账保护笔记",
            body="FICTIONAL_SMART_VIEW_PROTECTED_BODY",
            main_category="项目",
            sub_category="AI资料验收",
            sub_sub_category="无",
            tags="package4, linked",
            visibility="full",
            target_ids=("gale",),
        ),
    )
    window.receipt_inbox_service.discard_receipt(204)
    manifest = {
        "database_path": str(database_path),
        "material_note_ids": fixtures,
        "pending_accept_gale_receipt_id": 201,
        "pending_discard_receipt_id": 202,
        "protected_receipt_id": 203,
        "protected_note_id": protected.note.id,
        "deletable_processed_receipt_id": 204,
        "fictional_only": True,
    }
    (data_root / "candidate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    window.close()
    application.processEvents()
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
