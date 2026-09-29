"""Generate a deterministic Gale production mirror snapshot for regression tests."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.gale_service import GaleMirrorService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.gale_mirror import SQLiteGaleMirrorWriter
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


FIXED_UPDATED_AT = "2026-01-02T03:04:05+08:00"


def _seed(
    notes: NoteService,
    tags: TagService,
    metadata: AiAccessMetadataService,
    *,
    title: str,
    body: str,
    visibility: str,
    targets: tuple[str, ...],
) -> int:
    location = NoteLocation("Fixture", title)
    note = notes.create_note(location)
    note = notes.save_note(
        note.id,
        title=title,
        content_markdown=body,
        location=location,
        expected_revision=note.revision,
    )
    tags.set_note_tags(note.id, (f"tag-{title}", "shared-tag"))
    metadata.set_note_visibility(note.id, visibility)
    metadata.set_note_targets(note.id, targets)
    return note.id


def _rows(connection: sqlite3.Connection, query: str) -> list[list[object]]:
    return [list(row) for row in connection.execute(query).fetchall()]


def generate_snapshot(work_root: Path) -> dict[str, object]:
    work_root.mkdir(parents=True)
    rules = work_root / "local_redaction_rules.json"
    rules.write_text(
        json.dumps({"FICTIONAL_GALE_SECRET": "[GALE_MASKED]"}),
        encoding="utf-8",
    )
    connections = initialize_database(work_root / "s_notes.db")
    directories = SQLiteDirectoriesRepository(connections)
    note_repository = SQLiteNotesRepository(connections, directories)
    tag_repository = SQLiteTagsRepository(connections)
    notes = NoteService(note_repository)
    tags = TagService(tag_repository)
    metadata = AiAccessMetadataService(note_repository)
    audit = AiAuditService(SQLiteAiAuditRepository(connections))

    _seed(
        notes,
        tags,
        metadata,
        title="common-full",
        body="# Common\n\nCanonical **Markdown** body.",
        visibility="full",
        targets=("common",),
    )
    _seed(
        notes,
        tags,
        metadata,
        title="gale-redacted",
        body="FICTIONAL_GALE_SECRET must be masked.",
        visibility="redacted",
        targets=("gale",),
    )
    _seed(
        notes,
        tags,
        metadata,
        title="clawbot-only",
        body="Clawbot payload must not enter Gale.",
        visibility="full",
        targets=("clawbot",),
    )
    _seed(
        notes,
        tags,
        metadata,
        title="hidden-gale",
        body="Hidden payload must not enter Gale.",
        visibility="hidden",
        targets=("gale",),
    )
    with connections.write() as connection:
        connection.execute(
            "UPDATE notes SET created_at = ?, updated_at = ?",
            (FIXED_UPDATED_AT, FIXED_UPDATED_AT),
        )

    service = GaleMirrorService(
        note_repository,
        AiSafeReadService(rules, body_renderer=markdown_to_plain_text),
        SQLiteGaleMirrorWriter(),
        audit,
        directories=DirectoryService(directories, note_repository),
        tags=tags,
        ai_metadata=metadata,
    )
    output = work_root / "gale-mirror.db"
    result = service.generate(output)
    verification = service.verify(output, expected_note_count=result.note_count)

    with closing(sqlite3.connect(output)) as connection:
        schema = _rows(
            connection,
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """,
        )
        metadata_rows = _rows(
            connection,
            """
            SELECT meta_key, meta_value
            FROM mirror_metadata
            WHERE meta_key != 'generated_at'
            ORDER BY meta_key
            """,
        )
        payload = {
            "mirror_notes": _rows(
                connection,
                "SELECT * FROM mirror_notes ORDER BY mirror_note_id",
            ),
            "mirror_notes_fts": _rows(
                connection,
                """
                SELECT rowid, title, body, directory, tags
                FROM mirror_notes_fts ORDER BY rowid
                """,
            ),
            "mirror_directories": _rows(
                connection,
                "SELECT * FROM mirror_directories ORDER BY directory_id",
            ),
            "mirror_tags": _rows(
                connection,
                "SELECT * FROM mirror_tags ORDER BY name",
            ),
            "mirror_ai_visibility_options": _rows(
                connection,
                "SELECT * FROM mirror_ai_visibility_options ORDER BY value",
            ),
            "mirror_ai_targets": _rows(
                connection,
                "SELECT * FROM mirror_ai_targets ORDER BY target_id",
            ),
        }

    return {
        "result": {
            "schema_version": result.schema_version,
            "target_actor": result.target_actor,
            "note_count": result.note_count,
            "full_note_count": result.full_note_count,
            "redacted_note_count": result.redacted_note_count,
            "file_mode": result.file_mode,
        },
        "verification": {
            "note_count": verification.note_count,
            "fts_count": verification.fts_count,
            "full_note_count": verification.full_note_count,
            "redacted_note_count": verification.redacted_note_count,
            "file_mode": verification.file_mode,
        },
        "schema": schema,
        "metadata_without_generated_at": metadata_rows,
        "payload": payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.snapshot.write_text(
        json.dumps(
            generate_snapshot(arguments.work_root),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
