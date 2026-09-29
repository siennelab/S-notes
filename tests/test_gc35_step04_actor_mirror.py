from __future__ import annotations

import json
from contextlib import closing
import os
from unittest.mock import patch
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
from src.snotes.domain.actor_mirror import ActorMirrorError
from src.snotes.storage.actor_mirror import verify_actor_mirror


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
        with closing(sqlite3.connect(path)) as connection, connection:
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
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE mirror_metadata SET meta_value='999' WHERE meta_key='note_count'")
        with self.assertRaises(Exception):
            self.service.verify(path, expected_actor="clawbot")

    def test_candidate_with_cross_actor_grant_is_rejected(self):
        path = self.root / "cross-actor.db"
        self.service.generate("claude-code", path)
        with closing(sqlite3.connect(path)) as connection, connection:
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

    def test_concurrent_grant_change_uses_one_snapshot(self):
        # WAL lets the writer commit between reader statements, deterministically.
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
        for actor in ("claude-code", "clawbot"):
            with self.subTest(actor=actor):
                note_id = self._note("Race", "FIXTURE_SECRET", "full", ["clawbot"])
                original = self.notes._read_ai_target_ids
                def change_permission(connection, current_id):
                    if current_id == note_id:
                        self.assertTrue(connection.in_transaction)
                        with closing(sqlite3.connect(self.db)) as writer, writer:
                            writer.execute("UPDATE notes SET ai_visibility='redacted', updated_at='2030-01-01' WHERE id=?", (note_id,))
                            writer.execute("DELETE FROM note_ai_targets WHERE note_id=?", (note_id,))
                            writer.execute("INSERT INTO note_ai_targets(note_id,target_id,created_at) VALUES (?,'claude-code','fixture')", (note_id,))
                    return original(connection, current_id)
                path = self.root / f"race-{actor}.db"
                with patch.object(self.notes, "_read_ai_target_ids", side_effect=change_permission):
                    self.service.generate(actor, path)
                with closing(sqlite3.connect(path)) as mirror, mirror:
                    row = mirror.execute("SELECT visibility,safe_text,safe_markdown,source_updated_at FROM mirror_notes WHERE source_note_id=?", (note_id,)).fetchone()
                if actor == "claude-code":
                    self.assertIsNone(row)  # Entire pre-change snapshot denies Claude.
                else:
                    self.assertEqual(row[:3], ("full", "FIXTURE_SECRET", "FIXTURE_SECRET"))
                    self.assertNotEqual(row[3], "2030-01-01")
                # Entire post-change snapshot permits only redacted Claude data.
                self.service.generate(actor, path)
                with closing(sqlite3.connect(path)) as mirror, mirror:
                    row = mirror.execute("SELECT visibility,safe_text,safe_markdown,source_updated_at FROM mirror_notes WHERE source_note_id=?", (note_id,)).fetchone()
                if actor == "claude-code":
                    self.assertEqual(row, ("redacted", "[MASKED]", None, "2030-01-01"))
                else:
                    self.assertIsNone(row)

    def test_redacted_literal_markdown_is_rendered_once(self):
        cases = (
            ("`**literal**` FIXTURE_SECRET", "**literal** [MASKED]"),
            ("`[text](https://example.test)` FIXTURE_SECRET", "[text](https://example.test) [MASKED]"),
            (r"\*\*literal\*\* FIXTURE_SECRET", "**literal** [MASKED]"),
            (":size[`**literal**`]{size=20} FIXTURE_SECRET", "**literal** [MASKED]"),
        )
        for body, expected in cases:
            with self.subTest(body=body):
                note_id = self._note("Literal", body, "redacted", ["common"])
                path = self.root / "literal.db"
                self.service.generate("claude-code", path)
                with closing(sqlite3.connect(path)) as connection, connection:
                    row = connection.execute("SELECT safe_text,safe_markdown FROM mirror_notes WHERE source_note_id=?", (note_id,)).fetchone()
                self.assertEqual(row, (expected, None))

    def _assert_candidate_rejected(self, sql, code):
        path = self.root / "good.db"
        self.service.generate("claude-code", path)
        previous = path.read_bytes()
        original = verify_actor_mirror
        def corrupt(candidate, **kwargs):
            with closing(sqlite3.connect(candidate)) as connection, connection:
                for statement in sql:
                    connection.execute(statement)
            return original(candidate, **kwargs)
        with patch("src.snotes.storage.actor_mirror.verify_actor_mirror", side_effect=corrupt):
            with self.assertRaises(ActorMirrorError) as caught:
                self.service.generate("claude-code", path)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(path.read_bytes(), previous)
        self.assertFalse(list(self.root.glob(".*.candidate*")))

    def test_extra_column_rejected_without_replacing_good_mirror(self):
        self._assert_candidate_rejected(["ALTER TABLE mirror_notes ADD COLUMN raw_extra TEXT"], "schema_mismatch")

    def test_missing_index_rejected_without_replacing_good_mirror(self):
        self._assert_candidate_rejected(["DROP INDEX idx_actor_mirror_source_updated"], "schema_mismatch")

    def test_wrong_source_schema_rejected_without_replacing_good_mirror(self):
        self._assert_candidate_rejected(["UPDATE mirror_metadata SET meta_value='wrong' WHERE meta_key='source_schema'"], "source_schema_mismatch")

    def test_wrong_actor_rejected_without_replacing_good_mirror(self):
        self._assert_candidate_rejected(["UPDATE mirror_metadata SET meta_value='clawbot' WHERE meta_key='target_actor'"], "target_actor_mismatch")

    def test_payload_hash_mismatch_rejected_without_replacing_good_mirror(self):
        self._assert_candidate_rejected(["UPDATE mirror_notes SET safe_markdown='changed' WHERE visibility='full'"], "hash_mismatch")

    def test_source_path_and_aliases_rejected_before_writer(self):
        alias = self.root / "hardlink.db"
        os.link(self.db, alias)
        symlink = self.root / "symlink.db"
        symlink.symlink_to(self.db)
        before = self.db.read_bytes()
        for output in (self.db, self.root / "." / "fixture.db", alias, symlink):
            with self.subTest(output=output):
                with patch.object(self.service._writer, "write") as writer:
                    with self.assertRaises(ActorMirrorError) as caught:
                        self.service.generate("claude-code", output)
                    self.assertEqual(caught.exception.code, "source_output_same_file")
                    writer.assert_not_called()
                self.assertEqual(self.db.read_bytes(), before)
                self.assertEqual(output.read_bytes(), before)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM notes").fetchone()[0], 5)
            self.assertIn("content_markdown", [row[1] for row in connection.execute("PRAGMA table_info(notes)")])

    def test_unavailable_redaction_preserves_good_mirror(self):
        path = self.root / "good.db"
        self.service.generate("claude-code", path)
        before = path.read_bytes()
        self.rules.write_text("invalid", encoding="utf-8")
        with self.assertRaises(ActorMirrorError) as caught:
            self.service.generate("claude-code", path)
        self.assertEqual(caught.exception.code, "redaction_rules_unavailable")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
