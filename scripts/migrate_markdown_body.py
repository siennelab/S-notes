#!/usr/bin/env python3
"""Explicit offline body migration. No default path, no runtime imports.

Usage: .venv/bin/python scripts/migrate_markdown_body.py --db synthetic.db
The caller must stop writers and retain a backup for any valuable database.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.snotes.migration.legacy_qt import legacy_qt_html_to_markdown


def migrate(database: Path) -> dict:
    # mode=rw refuses misspelled/nonexistent paths rather than creating a database.
    connection = sqlite3.connect(f'{database.resolve().as_uri()}?mode=rw', uri=True)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        has_meta = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone()
        marker = connection.execute("SELECT meta_value FROM schema_meta WHERE meta_key='body_format'").fetchone() if has_meta else None
        if marker and marker[0] == 'markdown':
            raise ValueError('Already Markdown: repeat migration refused')
        if marker:
            raise ValueError('Unknown body_format: migration refused')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(notes)')}
        if 'content_html' not in columns or 'content_markdown' in columns:
            raise ValueError('Expected legacy content_html schema')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source integrity check failed')
        report = []
        converted = []
        for note_id, body in connection.execute('SELECT id, content_html FROM notes ORDER BY id'):
            result = legacy_qt_html_to_markdown(body or '')
            converted.append((result.markdown, note_id))
            report.append({'note_id': note_id, 'known_losses': result.known_losses})
        connection.execute("ALTER TABLE notes ADD COLUMN content_markdown TEXT NOT NULL DEFAULT ''")
        connection.executemany('UPDATE notes SET content_markdown=? WHERE id=?', converted)
        connection.execute('ALTER TABLE notes DROP COLUMN content_html')
        if 'body_rich_text' in columns:
            connection.execute('ALTER TABLE notes DROP COLUMN body_rich_text')
        connection.execute('CREATE TABLE IF NOT EXISTS schema_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)')
        connection.execute("INSERT INTO schema_meta VALUES ('body_format', 'markdown')")
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Foreign key check failed')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Result integrity check failed')
        connection.commit()
        return {'body_format': 'markdown', 'note_count': len(report), 'notes': report}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.db), ensure_ascii=False, indent=2))
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Migration refused/failed: {error}\n')


if __name__ == '__main__':
    main()
