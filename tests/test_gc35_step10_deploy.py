"""Focused checks of the deployed outbox SSH dispatcher and SQLite backup."""
from __future__ import annotations

import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from src.services.gale_submission_outbox import (
    GaleSubmissionOutboxError, enqueue_proposal, get_operation_status,
    initialize_outbox,
)

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    specification = importlib.util.spec_from_file_location(
        "step10_" + name, ROOT / "deploy/greencloud/outbox" / (name + ".py"))
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


transport = load("transport")
backup = load("backup")


class Step10DeployTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step10-deploy-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "outbox.db"
        initialize_outbox(self.database, generalized=True)

    def tearDown(self):
        self.temporary.cleanup()

    def enqueue(self):
        return enqueue_proposal(self.database, source_actor="claude-code", request_id="fixture",
                                operation_type="create", payload={"title": "Fictional", "content": "Body"})[0]

    def test_cloud_transport_exports_acknowledges_and_records_terminal_idempotently(self):
        item = self.enqueue()
        exported = transport.dispatch("export", {}, database=self.database)
        self.assertEqual(exported["submissions"][0]["submission_uuid"], item.submission_uuid)
        self.assertEqual(exported["submissions"][0]["payload"]["content"], "Body")
        ack = {"submission_uuid": item.submission_uuid, "local_receipt_id": "mac-receipt-fixture"}
        self.assertFalse(transport.dispatch("acknowledge", ack, database=self.database)["duplicate"])
        self.assertTrue(transport.dispatch("acknowledge", ack, database=self.database)["duplicate"])
        receipt = {"source_actor": "claude-code", "request_id": "fixture", "terminal_state": "rejected"}
        self.assertEqual(transport.dispatch("receipt", receipt, database=self.database)["delivery_state"], "rejected")
        self.assertTrue(transport.dispatch("receipt", receipt, database=self.database)["duplicate"])
        self.assertTrue(transport.dispatch("acknowledge", ack, database=self.database)["duplicate"])
        self.assertEqual(get_operation_status(self.database, source_actor="claude-code", request_id="fixture").delivery_state,
                         "rejected")
        self.assertEqual(transport.dispatch("export", {}, database=self.database)["submissions"], [])

    def test_transport_rejects_arbitrary_paths_commands_actors_and_payload_fields(self):
        for action, request in (("delete", {}), ("export", {"database": "other.db"}),
                                ("export", {"limit": True}), ("acknowledge", {"source_actor": "gale"}),
                                ("receipt", {"source_actor": "unknown", "request_id": "fixture", "terminal_state": "accepted"})):
            with self.subTest(action=action, request=request), self.assertRaises(ValueError):
                transport.dispatch(action, request, database=self.database)
        with self.assertRaisesRegex(GaleSubmissionOutboxError, "submission_not_found"):
            transport.dispatch(
                "receipt",
                {
                    "source_actor": "gale",
                    "request_id": "fixture",
                    "terminal_state": "accepted",
                },
                database=self.database,
            )

    def test_backup_includes_wal_data_and_retains_only_generated_snapshots(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            self.enqueue()
            directory = self.root / "backups"
            directory.mkdir()
            unrelated = directory / "keep.txt"
            unrelated.write_text("fixture")
            for _ in range(3):
                backup.create_backup(self.database, directory, retain=2)
            self.assertEqual(len(list(directory.glob("outbox-2*.db"))), 2)
            self.assertTrue(unrelated.exists())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            latest = directory / "outbox-latest.db"
            self.assertEqual(stat.S_IMODE(latest.stat().st_mode), 0o600)
            with closing(sqlite3.connect(latest)) as snapshot:
                self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM submissions").fetchone()[0], 1)
                self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
