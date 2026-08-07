"""SQLite row projections into pure domain objects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.snotes.domain.models import Directory, Note


def note_from_row(row: Mapping[str, Any]) -> Note:
    return Note(
        id=int(row["id"]),
        note_uuid=str(row["note_uuid"]),
        revision=int(row["revision"]),
        directory_id=row["directory_id"],
        title=str(row["title"]),
        content_html=str(row["content_html"]),
        main_category=str(row["main_category"]),
        sub_category=str(row["sub_category"]),
        sub_sub_category=str(row["sub_sub_category"]),
        tags=str(row["tags"]),
        ai_visibility=str(row["ai_visibility"]),
        is_pinned=bool(row["is_pinned"]),
        is_deleted=bool(row["is_deleted"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        deleted_at=row["deleted_at"],
        trash_group_id=row["trash_group_id"],
    )


def directory_from_row(row: Mapping[str, Any]) -> Directory:
    return Directory(
        id=int(row["id"]),
        name=str(row["name"]),
        parent_id=row["parent_id"],
        sort_order=int(row["sort_order"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        is_deleted=bool(row["is_deleted"]),
        deleted_at=row["deleted_at"],
        trash_group_id=row["trash_group_id"],
    )
