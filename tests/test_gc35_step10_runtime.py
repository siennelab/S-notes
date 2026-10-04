"""Step 10 credential binding and protected WAL initialization, fictional only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from src.cloud_outbox import initialize_service_database, load_actor_credentials
from src.cloud_read_mcp import ActorBinding, binding_for_effective_uid, read_actor_credential
from src.snotes.transport.cloud_proposal_client import CloudProposalClient


class Step10RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step10-runtime-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_euid_binding_and_actor_credential_inode_checks(self):
        self.assertEqual(binding_for_effective_uid(2001).actor, "claude-code")
        self.assertEqual(binding_for_effective_uid(2002).actor, "clawbot")
        with self.assertRaisesRegex(RuntimeError, "unsupported_os_identity"):
            binding_for_effective_uid(0)
        token_path = self.root / "actor-token"
        token_path.write_text("fictional-token\n")
        token_path.chmod(0o400)
        binding = ActorBinding(os.geteuid(), "claude-code", self.root / "mirror.db", token_path)
        self.assertEqual(read_actor_credential(binding), "fictional-token")
        token_path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "credential_permissions_invalid"):
            read_actor_credential(binding)
        token_path.chmod(0o400)
        link = self.root / "linked-token"
        link.symlink_to(token_path)
        linked = ActorBinding(os.geteuid(), "claude-code", self.root / "mirror.db", link)
        with self.assertRaisesRegex(RuntimeError, "credential_unavailable"):
            read_actor_credential(linked)

    def test_server_uses_only_two_existing_actor_credentials(self):
        credential = self.root / "reminder-api.json"
        credential.write_text(json.dumps({
            "owner": "fictional-owner",
            "claude-code": "fictional-claude",
            "clawbot": "fictional-claw",
            "local_actor_credentials": {
                "clawbot": "fictional-mac-claw",
                "antigravity": "fictional-mac-antigravity",
            },
        }))
        credential.chmod(0o600)
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.root)}):
            self.assertEqual(load_actor_credentials(), {"fictional-claude": "claude-code",
                                                       "fictional-claw": "clawbot"})
            credential.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "cloud_outbox_credential_invalid"):
                load_actor_credentials()
        for endpoint in ("http://other:8767", "http://127.0.0.1:8766", "https://127.0.0.1:8767"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                CloudProposalClient(lambda: "fictional-claude", endpoint=endpoint)

    def test_new_service_database_is_generalized_wal_mode_0600(self):
        database = self.root / "data/outbox.db"
        initialize_service_database(database)
        self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(database.parent.stat().st_mode), 0o700)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (3,))
            connection.execute("SELECT COUNT(*) FROM submissions").fetchone()
            for suffix in ("-wal", "-shm"):
                self.assertEqual(stat.S_IMODE(Path(str(database) + suffix).stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
