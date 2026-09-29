from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.snotes.application.ai_actor_registry import AI_ACTOR_IDS, target_definition
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.actor_mirror_service import ActorMirrorService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.storage.actor_mirror import SQLiteActorMirrorWriter
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.domain.models import NoteLocation


class ActorMirrorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "fixture.db"
        self.connections = initialize_database(self.db)
        self.rules = self.root / "rules.json"
        self.rules.write_text(json.dumps({"FIXTURE_SECRET": "[MASKED]"}), encoding="utf-8")
        self.directories = SQLiteDirectoriesRepository(self.connections)
        self.notes = SQLiteNotesRepository(self.connections, self.directories)
        self.safe = AiSafeReadService(self.rules, body_renderer=markdown_to_plain_text)
        self.service = ActorMirrorService(self.notes, self.safe, SQLiteActorMirrorWriter())
        self.ids = {
            "common": self._note("Common", "# Full\n\n**bold**", "full", ["common"]),
            "claude": self._note("Claude", "Claude only", "full", ["claude-code"]),
            "clawbot": self._note("Clawbot", "Clawbot only", "full", ["clawbot"]),
            "hidden": self._note("Hidden", "no", "hidden", ["common"]),
            "redacted": self._note("Redacted", "## FIXTURE_SECRET", "redacted", ["common"]),
        }

    def tearDown(self):
        self.temp.cleanup()

    def _note(self, title, body, visibility, targets):
        note = self.notes.create(NoteLocation("fixture", "test"))
        self.notes.save(note.id, title=title, content_markdown=body, location=NoteLocation("fixture", "test"), tags=(), expected_revision=note.revision)
        self.notes.set_ai_visibility(note.id, visibility)
        catalog = tuple(
            (item.target_id, item.display_name, item.slug, item.selectable, item.system)
            for item in __import__("src.snotes.application.ai_actor_registry", fromlist=["AI_TARGET_REGISTRY"]).AI_TARGET_REGISTRY
        )
        self.notes.set_ai_targets(note.id, targets, catalog)
        return note.id

    def test_registry_and_actor_specific_generation_contract(self):
        self.assertIn("claude-code", AI_ACTOR_IDS)
        self.assertIn("clawbot", AI_ACTOR_IDS)
        self.assertEqual(target_definition("claude-code").target_id, "claude-code")
        outputs = {}
        for actor in ("claude-code", "clawbot"):
            path = self.root / f"{actor}.db"
            result = self.service.generate(actor, path)
            outputs[actor] = path
            self.assertEqual(result["target_actor"], actor)
            self.assertEqual(result["note_count"], 3)
            self.assertEqual(result["hash"], self.service.verify(path, expected_actor=actor)["hash"])
        for actor, path in outputs.items():
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                rows = {row["source_note_id"]: dict(row) for row in connection.execute("SELECT * FROM mirror_notes")}
                self.assertIn(self.ids["common"], rows)
                self.assertNotIn(self.ids["hidden"], rows)
                self.assertIn(self.ids["redacted"], rows)
                self.assertEqual(rows[self.ids["redacted"]]["safe_markdown"], None)
                self.assertNotIn("FIXTURE_SECRET", rows[self.ids["redacted"]]["safe_text"])
                self.assertEqual(rows[self.ids["redacted"]]["safe_text"], "[MASKED]")
                full = rows[self.ids["common"]]
                self.assertEqual(full["safe_markdown"], "# Full\n\n**bold**")
                self.assertEqual(full["safe_text"], markdown_to_plain_text(full["safe_markdown"]))
                self.assertEqual(full["dialect"], "snotes-markdown-v1")
                self.assertEqual(full["source_version"], note_version_token(full["source_note_id"], full["source_updated_at"]))
                self.assertEqual(rows[self.ids["claude" if actor == "claude-code" else "clawbot"]]["source_note_id"], self.ids["claude" if actor == "claude-code" else "clawbot"])
                self.assertNotIn(self.ids["clawbot" if actor == "claude-code" else "claude"], rows)
            finally:
                connection.close()

    def test_full_markdown_does_not_pass_through_redaction(self):
        note_id = self._note("Raw remains", "FIXTURE_SECRET `inline`", "full", ["claude-code"])
        path = self.root / "full.db"
        self.service.generate("claude-code", path)
        with sqlite3.connect(path) as connection:
            row = connection.execute("SELECT safe_text,safe_markdown FROM mirror_notes WHERE source_note_id=?", (note_id,)).fetchone()
        self.assertEqual(row[0], "FIXTURE_SECRET inline")
        self.assertEqual(row[1], "FIXTURE_SECRET `inline`")

    def test_source_schema_marker_is_formal_markdown_marker(self):
        with self.connections.read() as connection:
            row = connection.execute("SELECT meta_value FROM schema_meta WHERE meta_key='body_format'").fetchone()
        self.assertEqual(row[0], "markdown")

    def test_corrupted_candidate_is_rejected(self):
        path = self.root / "broken.db"
        self.service.generate("clawbot", path)
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE mirror_metadata SET meta_value='999' WHERE meta_key='note_count'")
        with self.assertRaises(Exception):
            self.service.verify(path, expected_actor="clawbot")

    def test_candidate_with_cross_actor_grant_is_rejected(self):
        path = self.root / "cross-actor.db"
        self.service.generate("claude-code", path)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE mirror_notes SET ai_targets_json='[\"clawbot\"]' WHERE source_note_id=?",
                (self.ids["common"],),
            )
            connection.execute("UPDATE mirror_metadata SET meta_value='invalid' WHERE meta_key='hash'")
        with self.assertRaises(Exception):
            self.service.verify(path, expected_actor="claude-code")

    def test_gale_production_schema_source_remains_frozen(self):
        from src.snotes.storage import gale_mirror
        self.assertNotIn("safe_markdown", gale_mirror.SCHEMA)
        self.assertIn("body TEXT NOT NULL", gale_mirror.SCHEMA)


if __name__ == "__main__":
    unittest.main()
