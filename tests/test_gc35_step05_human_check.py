"""Small fictional-data check for the human gate's script-only title lookup."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.greencloud_human_check import lookup_title, main, TitleLookupError
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
