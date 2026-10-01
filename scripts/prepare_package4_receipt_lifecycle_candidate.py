#!/usr/bin/env python3
"""Prepare a fictional isolated DB for Package 4 human receipt acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields
from src.snotes.bootstrap import build_application, resolve_rebuild_data_root
from src.snotes.reminder_authority import resolve_reminder_authority
from src.snotes.storage.connection import SQLiteConnectionFactory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    arguments = parser.parse_args()
    data_root = resolve_rebuild_data_root(arguments.data_root)
    database_path = data_root / "s_notes.db"
    if database_path.exists():
        raise RuntimeError("Candidate database already exists; use a fresh isolated root")

    application, window = build_application(
        [],
        data_root=data_root,
        reminder_authority=resolve_reminder_authority(),
    )
    connections = SQLiteConnectionFactory(database_path)
    fixtures = (
        (101, "pending", "PENDING A · 接受我", "接受后应创建一篇虚构正式笔记"),
        (102, "pending", "PENDING B · 丢弃我", "丢弃后不应创建笔记或进入回收站"),
        (103, "pending", "PROCESSED · accepted + linked（应拒绝删除）", "查账保护样本"),
        (104, "pending", "PROCESSED · discarded（保留观察）", "已丢弃样本"),
        (105, "pending", "PROCESSED · discarded（允许删除）", "可删除样本"),
    )
    with connections.write() as connection:
        for receipt_id, status, title, body in fixtures:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, ai_targets, body, created_at, updated_at
                ) VALUES (
                    ?, 'FICTIONAL_CANDIDATE_RAW', 'parsed', ?, 'Package 4 Fixture',
                    '新增', ?, '项目', 'Package4验收', '无', 'package4, fictional',
                    'full', 'gale', ?, '2026-08-10T07:00:00',
                    '2026-08-10T08:00:00'
                )
                """,
                (receipt_id, status, title, body),
            )

    accepted = window.receipt_inbox_service.accept_receipt(
        103,
        AiReceiptAcceptFields(
            title="FICTIONAL · 已接受且关联的查账笔记",
            body="FICTIONAL_ACCEPTED_LINKED_BODY",
            main_category="项目",
            sub_category="Package4验收",
            sub_sub_category="无",
            tags="package4, linked",
            visibility="full",
            target_ids=("gale",),
        ),
    )
    window.receipt_inbox_service.discard_receipt(104)
    window.receipt_inbox_service.discard_receipt(105)
    manifest = {
        "database_path": str(database_path),
        "pending_accept_id": 101,
        "pending_discard_id": 102,
        "protected_receipt_id": 103,
        "protected_note_id": accepted.note.id,
        "processed_observe_id": 104,
        "processed_delete_id": 105,
        "fictional_only": True,
    }
    manifest_path = data_root / "candidate_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    window.close()
    application.processEvents()
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
