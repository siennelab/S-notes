#!/usr/bin/env python3
"""Create the isolated, fully fictional Package 2 human candidate database."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.snotes.application.note_service import NoteService
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database


def prepare(data_root: Path) -> tuple[Path, int]:
    root = data_root.expanduser().resolve()
    formal = Path("/Users/s/Library/Application Support/S-notes").resolve()
    if root == formal:
        raise RuntimeError("candidate root must never be the formal S-notes data root")
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "s_notes.db"
    connections = initialize_database(database_path)
    directories = SQLiteDirectoriesRepository(connections)
    notes = NoteService(SQLiteNotesRepository(connections, directories))
    existing = notes.list_active_notes()
    if existing:
        return database_path, len(existing)

    fictional = (
        (
            "虚构短笔记：晨间清单",
            "<p>这是完全虚构的候选数据。</p><p>买一束纸花，整理虚构书架。</p>",
        ),
        (
            "虚构富文本：排版验收",
            "<h1><span style='font-size:26px;font-weight:700'>虚构大标题</span></h1>"
            "<p><span style='font-size:11px'>11px 小字</span></p>"
            "<p><span style='font-size:13px'>13px</span> "
            "<span style='font-size:15px;font-weight:700'>15px 粗体</span> "
            "<span style='font-size:17px'>17px</span> "
            "<span style='font-size:20px;font-style:italic'>20px 斜体</span> "
            "<span style='font-size:23px'>23px</span> "
            "<span style='font-size:26px;text-decoration:line-through'>26px 删除线</span> "
            "<span style='font-size:32px'>32px</span></p>"
            "<ul><li>虚构列表甲</li><li>虚构列表乙</li></ul>"
            "<blockquote>虚构引用</blockquote><pre>fictional_code()</pre>"
            "<p><a href='https://example.invalid'>虚构链接</a></p>",
        ),
        (
            "虚构连续 Enter：32px 与粗体",
            "<p><span style='font-size:32px;font-weight:700'>"
            "请把光标放在本段末尾，连续按 Enter 验证每次都进入新行。"
            "</span></p>",
        ),
        (
            "虚构连续 Enter：标题退出",
            "<h1><span style='font-size:26px;font-weight:700'>"
            "请在标题末尾连续按 Enter"
            "</span></h1><p>标题后应进入普通正文，后续 Enter 不应被吞。</p>",
        ),
        (
            "虚构长笔记：Enter 滚动验收",
            "<p><span style='font-size:20px'>"
            + " ".join(
                f"虚构单段词语{index:03d}" for index in range(700)
            )
            + "</span></p>"
            + "".join(
                f"<p><span style='font-size:20px'>虚构长文第 {index:02d} 段："
                "用于测试光标靠近窗口下部时连续 Return。</span></p>"
                for index in range(1, 61)
            ),
        ),
    )
    for title, content_html in fictional:
        note = notes.create_note()
        notes.save_note(
            note.id,
            title=title,
            content_html=content_html,
            location=note.location,
            expected_revision=note.revision,
        )
    return database_path, len(fictional)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    database_path, count = prepare(args.data_root)
    print(f"CANDIDATE_DB={database_path}")
    print(f"FICTIONAL_ACTIVE_NOTES={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
