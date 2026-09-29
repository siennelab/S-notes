"""Small fictional-data checks for the human gate's title lookup and summary output."""
from contextlib import asynccontextmanager, redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.greencloud_human_check import (
    PREVIEW_CHARS, TitleLookupError, lookup_title, main, summarize_note,
)
from tests.gc35_step05_cloud_fixture import _create_note
from src.snotes.storage.schema import initialize_database
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository


class HumanTitleLookupTest(unittest.TestCase):
    def test_title_lookup_boundaries_and_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "fictional.db"
            connections = initialize_database(database)
            notes = SQLiteNotesRepository(connections, SQLiteDirectoriesRepository(connections))

            def create(title, visibility="full", targets=("common",)):
                return _create_note(notes, title=title, markdown="Fictional body",
                                    visibility=visibility, targets=targets)

            chosen = create("精确标题 ' %")
            create("精确标题 ' % extra")
            hidden = create("hidden", "hidden")
            create("private", targets=("claude-code",))
            redacted = create("redacted", "redacted")
            create("duplicate")
            create("duplicate")
            deleted = create("deleted")
            with connections.write() as connection:
                connection.execute("UPDATE notes SET is_deleted=1 WHERE id=?", (deleted,))
            before = database.read_bytes()
            self.assertEqual(lookup_title(database, "精确标题 ' %"), chosen)
            self.assertEqual(lookup_title(database, "redacted"), redacted)
            for title, code in (("missing", "note_not_found"), ("deleted", "note_not_found"),
                                ("hidden", "note_not_eligible_common"),
                                ("private", "note_not_eligible_common"),
                                ("duplicate", "ambiguous_title")):
                with self.subTest(title=title), self.assertRaises(TitleLookupError) as caught:
                    lookup_title(database, title)
                self.assertEqual(str(caught.exception), code)
                if title == "duplicate":
                    self.assertEqual(len(caught.exception.candidates), 2)
                    self.assertNotIn("content_markdown", caught.exception.candidates[0])
            self.assertEqual(database.read_bytes(), before)
            with patch("scripts.greencloud_human_check.DEFAULT_PATHS") as paths, \
                 patch("scripts.greencloud_human_check.publish", return_value=0) as publish, \
                 patch("scripts.greencloud_human_check.read_note") as read:
                paths.database = database
                self.assertEqual(main(["--title", "精确标题 ' %"]), 0)
                read.assert_awaited_once_with(chosen)
                read.reset_mock()
                self.assertEqual(main([str(chosen)]), 0)
                read.assert_awaited_once_with(chosen)
                publish.reset_mock()
                self.assertEqual(main(["--title", "missing"]), 2)
                publish.assert_not_called()


class HumanCheckOutputTest(unittest.TestCase):
    BODY = "第一行 fictional 修改标记\n" + "正文" * 300 + "TAIL-MARKER-NOT-PRINTED"

    def _run_with_fake_mcp(self, argv, database):
        calls = []

        @asynccontextmanager
        async def fake_stdio_client(server):
            calls.append(server.args[server.args.index("-u") + 1])
            yield None, None

        body = self.BODY

        class FakeSession:
            def __init__(self, _reader, _writer):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def initialize(self):
                return None

            async def call_tool(self, name, arguments):
                assert name == "get_note"
                return SimpleNamespace(structuredContent={
                    "status": "ok", "note_id": arguments["note_id"], "title": "Fictional title",
                    "safe_text": body, "directory": "", "tags": [],
                    "updated_at": "2026-09-29T00:00:00", "version": "v-fictional",
                    "visibility": "full",
                })

        stdout = io.StringIO()
        with patch("scripts.greencloud_human_check.DEFAULT_PATHS") as paths, \
             patch("scripts.greencloud_human_check.publish", return_value=0), \
             patch("scripts.greencloud_human_check.stdio_client", fake_stdio_client), \
             patch("scripts.greencloud_human_check.ClientSession", FakeSession), \
             redirect_stdout(stdout):
            paths.database = database
            status = main(argv)
        return status, calls, stdout.getvalue()

    def test_both_entries_print_summary_without_full_safe_text(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "fictional.db"
            connections = initialize_database(database)
            notes = SQLiteNotesRepository(connections, SQLiteDirectoriesRepository(connections))
            note_id = _create_note(notes, title="Human gate fictional", markdown="Fictional body",
                                   visibility="full", targets=("common",))
            for argv in ([str(note_id)], ["--title", "Human gate fictional"]):
                with self.subTest(argv=argv):
                    status, calls, output = self._run_with_fake_mcp(argv, database)
                    self.assertEqual(status, 0)
                    self.assertEqual(calls, ["claude", "openclaw"])
                    for field in ("actor=claude", "actor=openclaw", "status=ok",
                                  f"note_id={note_id}", "title=Fictional title",
                                  "version=v-fictional", "updated_at=2026-09-29T00:00:00",
                                  "visibility=full", f"safe_text_length={len(self.BODY)}",
                                  "preview=第一行 fictional 修改标记 正文"):
                        self.assertIn(field, output)
                    self.assertNotIn(self.BODY, output)
                    self.assertNotIn("TAIL-MARKER-NOT-PRINTED", output)
                    for line in output.splitlines():
                        if line.startswith("preview="):
                            self.assertLessEqual(len(line), len("preview=") + PREVIEW_CHARS + 1)

    def test_empty_and_failed_payload_summaries(self):
        empty = summarize_note("claude", {"status": "ok", "note_id": 1, "safe_text": ""})
        self.assertIn("safe_text_length=0", empty)
        self.assertIn("preview=", empty.splitlines())
        failed = summarize_note("openclaw", {"status": "not_found", "note_id": 9})
        self.assertEqual(failed, "actor=openclaw\nstatus=not_found\nnote_id=9")
