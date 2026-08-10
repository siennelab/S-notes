"""Shared SQLite writes for the single stable AI target registry."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import AiTargetCatalogEntry


def sync_ai_target_catalog(
    connection,
    target_catalog: Sequence[AiTargetCatalogEntry],
    timestamp: str,
) -> None:
    for target_id, display_name, slug, system, selectable in target_catalog:
        connection.execute(
            """
            INSERT INTO ai_targets (
                target_id, display_name, slug, is_system, is_enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                display_name = excluded.display_name,
                slug = excluded.slug,
                is_system = excluded.is_system,
                is_enabled = excluded.is_enabled,
                updated_at = excluded.updated_at
            """,
            (
                target_id,
                display_name,
                slug,
                1 if system else 0,
                1 if selectable else 0,
                timestamp,
                timestamp,
            ),
        )


def replace_note_ai_targets(
    connection,
    note_id: int,
    target_ids: Sequence[str],
    timestamp: str,
) -> None:
    connection.execute("DELETE FROM note_ai_targets WHERE note_id = ?", (note_id,))
    for target_id in target_ids:
        connection.execute(
            """
            INSERT OR IGNORE INTO note_ai_targets (
                note_id, target_id, created_at
            ) VALUES (?, ?, ?)
            """,
            (note_id, target_id, timestamp),
        )
