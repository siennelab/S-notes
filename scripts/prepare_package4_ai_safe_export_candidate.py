#!/usr/bin/env python3
"""Prepare fictional AI-safe export and audit acceptance fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.snotes.bootstrap import build_application, resolve_rebuild_data_root
from src.snotes.reminder_authority import resolve_reminder_authority


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    arguments = parser.parse_args()
    data_root = resolve_rebuild_data_root(arguments.data_root)
    database_path = data_root / "s_notes.db"
    if database_path.exists():
        raise RuntimeError("Candidate database already exists; use a fresh root")

    application, window = build_application(
        [],
        data_root=data_root,
        reminder_authority=resolve_reminder_authority(),
    )
    rules_path = data_root / "config" / "local_redaction_rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        json.dumps(
            {
                "replacements": [
                    {
                        "from": "FICTIONAL_EXPORT_CANARY",
                        "to": "[FICTIONAL_MASKED]",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def add_note(
        title: str,
        body: str,
        visibility: str,
        targets: tuple[str, ...],
    ) -> int:
        note = window.note_service.create_note()
        note = window.note_service.save_note(
            note.id,
            title=title,
            content_markdown=body,
            location=note.location,
            expected_revision=note.revision,
        )
        window.tag_service.set_note_tags(note.id, ("package4", "fictional"))
        window.ai_access_metadata_service.set_note_visibility(note.id, visibility)
        window.ai_access_metadata_service.set_note_targets(note.id, targets)
        return note.id

    fixtures = {
        "common_full": add_note(
            "SAFE EXPORT · 通用 full",
            "FICTIONAL common safe body",
            "full",
            ("common",),
        ),
        "gale_full": add_note(
            "SAFE EXPORT · Gale full",
            "FICTIONAL Gale safe body",
            "full",
            ("gale",),
        ),
        "ke_only": add_note(
            "EXCLUDED FROM GALE · Ke only",
            "FICTIONAL Ke-only body",
            "full",
            ("ke-den",),
        ),
        "gale_redacted": add_note(
            "SAFE EXPORT · FICTIONAL_EXPORT_CANARY redacted",
            "FICTIONAL_EXPORT_CANARY must become masked",
            "redacted",
            ("gale",),
        ),
        "hidden_gale": add_note(
            "EXCLUDED FROM ALL · hidden Gale",
            "FICTIONAL hidden body",
            "hidden",
            ("gale",),
        ),
        "legacy_codex": add_note(
            "LEGACY ONLY · Codex",
            "FICTIONAL legacy Codex body",
            "full",
            ("codex",),
        ),
        "suspicious_gale": add_note(
            "SAFE EXPORT · suspicious confirmation",
            "FICTIONAL reserved documentation IP 203.0.113.77",
            "full",
            ("gale",),
        ),
    }
    manifest = {
        "database_path": str(database_path),
        "exports_path": str(data_root / "exports" / "ai_readbox"),
        "rules_path": str(rules_path),
        "fixtures": fixtures,
        "recommended_actor": "gale",
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
