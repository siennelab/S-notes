from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.actor_mirror_publish import main as publisher_main
from src.services.actor_mirror_publish_service import (
    ActorMirrorPublishError,
    ActorMirrorPublisher,
    PublishReceipt,
    SshActorMirrorTransport,
)
from src.snotes.application.ai_actor_registry import AI_TARGET_REGISTRY
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import (
    SQLiteDirectoriesRepository,
)
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_actor = ""

    def publish(
        self,
        *,
        actor: str,
        mirror_path: Path,
        file_sha256: str,
        content_hash: str,
    ) -> PublishReceipt:
        self.calls.append((actor, content_hash))
        if actor == self.fail_actor:
            raise RuntimeError("fixture transport failure")
        with closing(sqlite3.connect(mirror_path)) as connection:
            note_count = int(
                connection.execute(
                    "SELECT meta_value FROM mirror_metadata "
                    "WHERE meta_key='note_count'"
                ).fetchone()[0]
            )
        return PublishReceipt(actor, content_hash, note_count)


class ActorMirrorPublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "source.db"
        connections = initialize_database(self.database)
        directories = SQLiteDirectoriesRepository(connections)
        self.notes = SQLiteNotesRepository(connections, directories)
        self.rules = self.root / "rules.json"
        self.rules.write_text(
            json.dumps({"FIXTURE_SECRET": "[MASKED]"}),
            encoding="utf-8",
        )
        self.common_note = self._note(
            "Common", "# original", "full", ("common",)
        )
        self._note(
            "Claude", "Claude only", "full", ("claude-code",)
        )
        self._note(
            "Clawbot", "Clawbot only", "full", ("clawbot",)
        )
        self.transport = FakeTransport()
        self.publisher = ActorMirrorPublisher(
            source_database=self.database,
            redaction_rules=self.rules,
            mirror_directory=self.root / "mirrors",
            state_path=self.root / "state.json",
            lock_path=self.root / "publisher.lock",
            transport=self.transport,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _note(
        self,
        title: str,
        body: str,
        visibility: str,
        targets: tuple[str, ...],
    ):
        note = self.notes.create(NoteLocation("fixture", "publisher"))
        note = self.notes.save(
            note.id,
            title=title,
            content_markdown=body,
            location=NoteLocation("fixture", "publisher"),
            tags=(),
            expected_revision=note.revision,
        )
        self.notes.set_ai_visibility(note.id, visibility)
        catalog = tuple(
            (
                item.target_id,
                item.display_name,
                item.slug,
                item.selectable,
                item.system,
            )
            for item in AI_TARGET_REGISTRY
        )
        self.notes.set_ai_targets(note.id, targets, catalog)
        return self.notes.get(note.id)

    def test_publish_uses_read_only_source_and_skips_unchanged_content(self):
        source_before = self.database.read_bytes()
        first = self.publisher.publish()
        self.assertEqual(
            [result.status for result in first],
            ["published", "published", "published"],
        )
        self.assertEqual(
            [call[0] for call in self.transport.calls],
            ["claude-code", "clawbot", "gale"],
        )
        second = self.publisher.publish()
        self.assertEqual(
            [result.status for result in second],
            ["unchanged", "unchanged", "unchanged"],
        )
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(self.database.read_bytes(), source_before)
        with self.assertRaisesRegex(RuntimeError, "read-only"):
            with SQLiteConnectionFactory(
                self.database, read_only=True
            ).write():
                pass

    def test_failure_preserves_success_hash_and_next_run_retries(self):
        self.publisher.publish()
        initial = self.publisher.status()
        initial_clawbot = initial["actors"]["clawbot"][
            "last_success_hash"
        ]
        current = self.notes.get(self.common_note.id)
        self.notes.save(
            current.id,
            title=current.title,
            content_markdown="# changed",
            location=current.location,
            tags=current.tags,
            expected_revision=current.revision,
        )
        self.transport.fail_actor = "clawbot"
        failed = self.publisher.publish()
        self.assertEqual(
            [result.status for result in failed],
            ["published", "failed", "published"],
        )
        failure_state = self.publisher.status()["actors"]["clawbot"]
        self.assertEqual(
            failure_state["last_success_hash"], initial_clawbot
        )
        self.assertTrue(failure_state["needs_retry"])

        calls_before_retry = len(self.transport.calls)
        self.transport.fail_actor = ""
        retried = self.publisher.publish()
        self.assertEqual(
            [result.status for result in retried],
            ["unchanged", "published", "unchanged"],
        )
        self.assertEqual(len(self.transport.calls), calls_before_retry + 1)
        self.assertFalse(
            self.publisher.status()["actors"]["clawbot"]["needs_retry"]
        )

    def test_cli_accepts_only_explicit_fixture_paths(self):
        with patch(
            "src.actor_mirror_publish.SshActorMirrorTransport",
            return_value=self.transport,
        ):
            exit_code = publisher_main(
                [
                    "publish",
                    "--source-db",
                    str(self.database),
                    "--redaction-rules",
                    str(self.rules),
                    "--mirror-directory",
                    str(self.root / "cli-mirrors"),
                    "--state",
                    str(self.root / "cli-state.json"),
                    "--lock",
                    str(self.root / "cli.lock"),
                    "--identity",
                    str(self.root / "unused-key"),
                ]
            )
        self.assertEqual(exit_code, 0)

    def test_state_lock_and_mirror_cannot_alias_source_database(self):
        source_before = self.database.read_bytes()
        lock_alias = self.root / "source-lock-alias.db"
        lock_alias.hardlink_to(self.database)
        mirror_directory = self.root / "alias-mirrors"
        mirror_directory.mkdir()
        mirror_alias = mirror_directory / "claude-code.db"
        mirror_alias.hardlink_to(self.database)
        cases = (
            {
                "state_path": self.database,
                "lock_path": self.root / "safe-lock",
                "mirror_directory": self.root / "safe-mirrors-a",
            },
            {
                "state_path": self.root / "safe-state",
                "lock_path": lock_alias,
                "mirror_directory": self.root / "safe-mirrors-b",
            },
            {
                "state_path": self.root / "safe-state-c",
                "lock_path": self.root / "safe-lock-c",
                "mirror_directory": mirror_directory,
            },
        )
        for paths in cases:
            with self.subTest(paths=paths):
                publisher = ActorMirrorPublisher(
                    source_database=self.database,
                    redaction_rules=self.rules,
                    transport=self.transport,
                    **paths,
                )
                with self.assertRaises(ActorMirrorPublishError) as caught:
                    publisher.publish()
                self.assertEqual(
                    caught.exception.code, "source_output_same_file"
                )
                self.assertEqual(self.database.read_bytes(), source_before)


class SshActorMirrorTransportTest(unittest.TestCase):
    def test_protocol_and_ssh_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "mirror-key"
            identity.write_text("fixture", encoding="utf-8")
            identity.chmod(0o600)
            mirror = root / "mirror.db"
            mirror.write_bytes(b"fixture mirror bytes")
            file_hash = __import__("hashlib").sha256(
                mirror.read_bytes()
            ).hexdigest()
            content_hash = "a" * 64

            def fake_run(command, **kwargs):
                header = json.loads(kwargs["stdin"].readline())
                payload = kwargs["stdin"].read()
                self.assertEqual(header["actor"], "claude-code")
                self.assertEqual(header["size"], len(payload))
                self.assertEqual(header["sha256"], file_hash)
                self.assertEqual(payload, mirror.read_bytes())
                self.assertIn("IdentitiesOnly=yes", command)
                self.assertIn("StrictHostKeyChecking=yes", command)
                self.assertEqual(command[-1], "snotes-publish@optimal")
                self.assertNotIn("sudo", command)
                response = json.dumps(
                    {
                        "status": "published",
                        "actor": "claude-code",
                        "hash": content_hash,
                        "note_count": 3,
                    }
                ).encode("utf-8") + b"\n"
                return subprocess.CompletedProcess(command, 0, response, b"")

            transport = SshActorMirrorTransport(identity_file=identity)
            with patch("subprocess.run", side_effect=fake_run):
                receipt = transport.publish(
                    actor="claude-code",
                    mirror_path=mirror,
                    file_sha256=file_hash,
                    content_hash=content_hash,
                )
            self.assertEqual(receipt.note_count, 3)


if __name__ == "__main__":
    unittest.main()
