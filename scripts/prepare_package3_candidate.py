#!/usr/bin/env python3
"""Prepare an isolated Package 3 candidate using only fictional data."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.migration.legacy_qt import legacy_qt_html_to_markdown
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


FORMAL_DATA_ROOT = Path("/Users/s/Library/Application Support/S-notes").resolve()


def prepare(data_root: Path) -> tuple[Path, int, int, int]:
    root = data_root.expanduser().resolve()
    if root == FORMAL_DATA_ROOT or not root.is_relative_to(Path("/private/tmp")):
        raise RuntimeError("Package 3 candidate root must be inside /private/tmp")
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "s_notes.db"
    connections = initialize_database(database_path)
    directory_repository = SQLiteDirectoriesRepository(connections)
    note_repository = SQLiteNotesRepository(connections, directory_repository)
    tag_repository = SQLiteTagsRepository(connections)
    notes = NoteService(note_repository)
    directories = DirectoryService(directory_repository, note_repository)
    tags = TagService(tag_repository)
    existing = notes.list_active_notes()
    if existing:
        trash = directories.list_trash()
        return database_path, len(existing), len(trash.notes), len(trash.directories)

    def add(title: str, body: str, location: NoteLocation, note_tags=(), pinned=False):
        note = notes.create_note(location)
        note = notes.save_note(
            note.id,
            title=title,
            content_markdown=legacy_qt_html_to_markdown(body).markdown,
            location=location,
            expected_revision=note.revision,
        )
        if note_tags:
            tags.set_note_tags(note.id, note_tags)
        if pinned:
            notes.set_note_pinned(note.id, True)
        return notes.get_note(note.id)

    project = directories.create_directory("虚构项目")
    alpha = directories.create_directory("Alpha 发布", project.id)
    network = directories.create_directory("虚构网络")
    topology = directories.create_directory("拓扑", network.id)
    archive = directories.create_directory("候选整理区")

    add(
        "Alpha 发布清单",
        "<h2>虚构发布清单</h2><p>关联：[[虚构网络拓扑]]</p>"
        "<ul><li>检查 node.js</li><li>验证 C++ 示例</li></ul>",
        NoteLocation("虚构项目", "Alpha 发布", "无"),
        ("todo", "alpha", "候选"),
        pinned=True,
    )
    add(
        "虚构网络拓扑",
        "<p>这是一份完全虚构的拓扑。反向引用 [[Alpha 发布清单]]。</p>"
        "<p>技术词：wg-hi、v2rayN、10.0.0.1。</p>",
        NoteLocation("虚构网络", "拓扑", "无"),
        ("alpha", "网络"),
    )
    add(
        "多关键词搜索样本",
        "<p>正文同时包含 中文 与 Alpha，可测试顺序无关的 AND 搜索。</p>",
        NoteLocation("候选整理区", "无", "无"),
        ("搜索", "中文"),
    )
    add(
        "待办：虚构书架",
        "<p>整理纸质模型，完成后可取消固定或移动目录。</p>",
        NoteLocation("待办", "无", "无"),
        ("todo", "生活"),
    )
    add(
        "批量整理甲",
        "<p>请与“批量整理乙”一起多选并移动。</p>",
        NoteLocation("候选整理区", "无", "无"),
        ("批量",),
    )
    add(
        "批量整理乙",
        "<p>可以拖到“虚构项目 / Alpha 发布”。</p>",
        NoteLocation("候选整理区", "无", "无"),
        ("批量",),
    )

    trash_root = directories.create_directory("已删除虚构目录")
    trash_child = directories.create_directory("可展开子层", trash_root.id)
    add(
        "目录内可单独恢复",
        "<p>先展开回收站目录，再单独恢复此笔记。</p>",
        NoteLocation("已删除虚构目录", "可展开子层", "无"),
        ("回收站",),
    )
    directories.soft_delete_directory(trash_root.id)
    independent = add(
        "独立回收站笔记",
        "<p>可单独恢复或永久删除。</p>",
        NoteLocation("临时", "无", "无"),
        ("回收站",),
    )
    notes.soft_delete_note(independent.id)

    (root / "exports" / "markdown").mkdir(parents=True, exist_ok=True)
    (root / "exports" / "json").mkdir(parents=True, exist_ok=True)
    (root / "exports" / "backups").mkdir(parents=True, exist_ok=True)
    trash = directories.list_trash()
    return database_path, len(notes.list_active_notes()), len(trash.notes), len(trash.directories)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    database_path, active, trash_notes, trash_directories = prepare(args.data_root)
    print(f"CANDIDATE_DB={database_path}")
    print(f"FICTIONAL_ACTIVE_NOTES={active}")
    print(f"FICTIONAL_TRASH_NOTES={trash_notes}")
    print(f"FICTIONAL_TRASH_DIRECTORIES={trash_directories}")
    print(f"EXPORT_ROOT={database_path.parent / 'exports'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
