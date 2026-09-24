"""Step 2A scan contract, using only a temporary SQLite fixture and fake Milkdown."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.markdown_roundtrip_scan import _read_only_connection, scan_database


class Step02aRoundtripScanTests(unittest.TestCase):
    def test_scan_counts_differences_without_changing_source_or_reporting_bodies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-step02a-test-") as temporary:
            database = Path(temporary) / "notes.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE notes (id INTEGER PRIMARY KEY, content_markdown TEXT, "
                    "is_deleted INTEGER, revision INTEGER, updated_at TEXT)"
                )
                connection.executemany(
                    "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
                    [
                        (1, "unchanged-private-sentinel", 0, 4, "2026-01-01T00:00:00Z"),
                        (2, "changed-private-sentinel", 0, 7, "2026-01-02T00:00:00Z"),
                        (3, "deleted-private-sentinel", 1, 9, "2026-01-03T00:00:00Z"),
                    ],
                )

            def source_rows() -> list[tuple]:
                with closing(sqlite3.connect(database)) as connection:
                    return connection.execute(
                        "SELECT id, content_markdown, revision, updated_at "
                        "FROM notes ORDER BY id"
                    ).fetchall()

            before = source_rows()
            with closing(_read_only_connection(database)) as read_only:
                with self.assertRaises(sqlite3.OperationalError):
                    read_only.execute("UPDATE notes SET revision = revision + 1 WHERE id = 1")
            checked_ids: list[int | str] = []

            def fake_milkdown(items: list[dict]) -> list[dict]:
                results = []
                for item in items:
                    checked_ids.append(item["id"])
                    original = item["markdown"]
                    first = original + "\n" if item["id"] == 2 else original
                    results.append(
                        {"id": item["id"], "first": first,
                         "second": first, "stable": True}
                    )
                return results

            report = scan_database(database, checker=fake_milkdown)

            self.assertEqual(report["scanned_notes"], 2)
            self.assertEqual(report["zero_diff"], 1)
            self.assertEqual(report["diff_notes"], 1)
            self.assertEqual(sum(report["diff_types"].values()), 1)
            self.assertEqual(report["error_count"], 0)
            self.assertIn(1, checked_ids)
            self.assertIn(2, checked_ids)
            self.assertNotIn(3, checked_ids)
            self.assertEqual(source_rows(), before)
            self.assertTrue(report["source_unchanged"])
            serialized = json.dumps(report, ensure_ascii=False)
            for sentinel in ("unchanged-private-sentinel", "changed-private-sentinel", "deleted-private-sentinel"):
                self.assertNotIn(sentinel, serialized)


if __name__ == "__main__":
    unittest.main()
