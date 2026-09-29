"""The single SQLite connection-lifecycle owner for R2 storage."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SQLiteConnectionFactory:
    def __init__(self, database_path: Path, *, read_only: bool = False) -> None:
        self.database_path = Path(database_path)
        self.read_only = bool(read_only)

    def _open(self) -> sqlite3.Connection:
        mode = "ro" if self.read_only else "rw"
        connection = sqlite3.connect(
            f"{self.database_path.resolve().as_uri()}?mode={mode}",
            uri=True,
            timeout=5.0,
        )
        try:
            require_markdown_database(connection)
        except Exception:
            connection.close()
            raise
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise RuntimeError("read-only SQLite connection factory")
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def require_markdown_database(connection: sqlite3.Connection) -> None:
    """Fail closed for every active connection, including helper/MCP callers."""
    try:
        marker = connection.execute("SELECT meta_value FROM schema_meta WHERE meta_key='body_format'").fetchone()
        columns = {row[1] for row in connection.execute('PRAGMA table_info(notes)')}
    except sqlite3.DatabaseError as error:
        raise RuntimeError("数据库需要 Markdown migration；请显式运行 migration CLI。") from error
    if not marker or marker[0] != 'markdown' or 'content_markdown' not in columns or columns & {'content_html', 'body_rich_text'}:
        raise RuntimeError("数据库需要 Markdown migration；请显式运行 migration CLI。")
