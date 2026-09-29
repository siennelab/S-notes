from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.snotes.domain.actor_mirror import ActorMirrorRecord
from src.snotes.storage.actor_mirror import verify_actor_mirror, write_actor_mirror
from src.snotes.storage.cloud_mirror_receiver import (
    MAX_HEADER_BYTES,
    MAX_PAYLOAD_BYTES,
    MirrorPublishError,
    receive_publish,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CloudMirrorReceiverTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "mirror"
        for actor in ("claude-code", "clawbot"):
            actor_directory = self.root / actor
            actor_directory.mkdir(parents=True)
            os.chmod(actor_directory, 0o2750)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_valid_fixture_is_published_with_validated_metadata_hash(self):
        payload, metadata_hash = self._fixture("claude-code", "first")

        result = receive_publish(self._request("claude-code", payload), mirror_root=self.root)

        current = self.root / "claude-code" / "current.db"
        self.assertEqual(
            result,
            {
                "status": "published",
                "actor": "claude-code",
                "hash": metadata_hash,
                "note_count": 1,
            },
        )
        self.assertEqual(current.read_bytes(), payload)
        self.assertEqual(stat.S_IMODE(current.stat().st_mode), 0o640)
        with closing(sqlite3.connect(current)) as connection:
            self.assertEqual(
                connection.execute("SELECT title FROM mirror_notes").fetchone()[0],
                "first",
            )
        self.assertFalse(list(current.parent.glob(".incoming.*.candidate")))

    def test_damaged_candidate_is_rejected_and_old_current_remains(self):
        current = self._old_current("claude-code")
        payload, _ = self._fixture("claude-code", "damaged")
        damaged = bytearray(payload)
        damaged[-16:] = b"X" * 16

        with self.assertRaises(MirrorPublishError) as caught:
            receive_publish(
                self._request("claude-code", bytes(damaged)), mirror_root=self.root
            )

        self.assertEqual(caught.exception.reason, "candidate_rejected")
        self.assertEqual(current.read_bytes(), b"OLD_CURRENT")

    def test_actor_mismatch_is_rejected_and_old_current_remains(self):
        current = self._old_current("clawbot")
        claude_payload, _ = self._fixture("claude-code", "wrong actor")

        with self.assertRaises(MirrorPublishError) as caught:
            receive_publish(
                self._request("clawbot", claude_payload), mirror_root=self.root
            )

        self.assertEqual(caught.exception.reason, "candidate_rejected")
        self.assertEqual(current.read_bytes(), b"OLD_CURRENT")

    def test_protocol_rejections_leave_current_unchanged(self):
        current = self._old_current("claude-code")
        payload, _ = self._fixture("claude-code", "protocol")
        valid_digest = hashlib.sha256(payload).hexdigest()
        cases = {
            "no newline": b'{}',
            "oversized header": b"{" + (b" " * MAX_HEADER_BYTES) + b"\n",
            "invalid json": b"{]\n",
            "extra field": self._raw_request(
                {"actor": "claude-code", "size": len(payload), "sha256": valid_digest, "x": 1},
                payload,
            ),
            "unsupported actor": self._raw_request(
                {"actor": "common", "size": len(payload), "sha256": valid_digest}, payload
            ),
            "boolean size": self._raw_request(
                {"actor": "claude-code", "size": True, "sha256": valid_digest}, payload
            ),
            "oversized payload declaration": self._raw_request(
                {"actor": "claude-code", "size": MAX_PAYLOAD_BYTES + 1, "sha256": valid_digest},
                b"",
            ),
            "uppercase digest": self._raw_request(
                {"actor": "claude-code", "size": len(payload), "sha256": valid_digest.upper()},
                payload,
            ),
            "truncated": self._raw_request(
                {"actor": "claude-code", "size": len(payload) + 1, "sha256": valid_digest},
                payload,
            ),
            "trailing data": self._request(
                "claude-code", payload, suffix=b"x"
            ).getvalue(),
            "checksum mismatch": self._raw_request(
                {"actor": "claude-code", "size": len(payload), "sha256": "0" * 64}, payload
            ),
        }

        for label, request in cases.items():
            with self.subTest(label=label), self.assertRaises(MirrorPublishError):
                receive_publish(BytesIO(request), mirror_root=self.root)
            self.assertEqual(current.read_bytes(), b"OLD_CURRENT")
            self.assertFalse(list(current.parent.glob(".incoming.*.candidate")))

    def test_same_actor_concurrent_publishes_serialize_validation(self):
        first, _ = self._fixture("claude-code", "first concurrent")
        second, _ = self._fixture("claude-code", "second concurrent")
        start = threading.Barrier(2)
        guard = threading.Lock()
        active = 0
        maximum_active = 0

        def tracked_verify(*args, **kwargs):
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            try:
                return verify_actor_mirror(*args, **kwargs)
            finally:
                with guard:
                    active -= 1

        def publish(payload):
            start.wait()
            return receive_publish(self._request("claude-code", payload), mirror_root=self.root)

        with patch(
            "src.snotes.storage.cloud_mirror_receiver.verify_actor_mirror",
            side_effect=tracked_verify,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(publish, (first, second)))

        self.assertEqual(maximum_active, 1)
        self.assertEqual([item["status"] for item in results], ["published", "published"])
        self.assertIn((self.root / "claude-code" / "current.db").read_bytes(), (first, second))

    def test_actor_locks_are_independent(self):
        claude, _ = self._fixture("claude-code", "claude")
        clawbot, _ = self._fixture("clawbot", "clawbot")
        start = threading.Barrier(2)
        both_validating = threading.Barrier(2)

        def synchronized_verify(*args, **kwargs):
            both_validating.wait(timeout=2)
            return verify_actor_mirror(*args, **kwargs)

        def publish(actor, payload):
            start.wait()
            return receive_publish(self._request(actor, payload), mirror_root=self.root)

        with patch(
            "src.snotes.storage.cloud_mirror_receiver.verify_actor_mirror",
            side_effect=synchronized_verify,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(publish, "claude-code", claude),
                executor.submit(publish, "clawbot", clawbot),
            )
            results = [future.result(timeout=4) for future in futures]

        self.assertEqual({item["actor"] for item in results}, {"claude-code", "clawbot"})

    def test_forced_command_entry_rejects_original_command_before_sudo(self):
        entry = PROJECT_ROOT / "deploy" / "greencloud" / "snotes-mirror-ssh-entry"
        result = subprocess.run(
            ["/bin/sh", str(entry)],
            env={"PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": "uname -a"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 64)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "error", "reason": "command_rejected"},
        )
        self.assertEqual(result.stderr, "")

    def test_deployment_wrappers_have_valid_shell_syntax(self):
        for name in ("snotes-mirror-ssh-entry", "snotes-mirror-receiver"):
            path = PROJECT_ROOT / "deploy" / "greencloud" / name
            result = subprocess.run(
                ["/bin/sh", "-n", str(path)], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def _fixture(self, actor: str, title: str) -> tuple[bytes, str]:
        output = Path(self.temporary_directory.name) / f"{actor}-{title}.db"
        record = ActorMirrorRecord(
            source_note_id=1,
            title=title,
            safe_text=f"safe {title}",
            safe_markdown=f"# {title}",
            directory="Fixture",
            tags=("fixture",),
            source_updated_at="2026-09-29T00:00:00+08:00",
            source_version="fixture-version",
            visibility="full",
            redaction_count=0,
            ai_visibility="allowlisted",
            ai_targets=(actor,),
        )
        result = write_actor_mirror(
            (record,),
            output,
            actor=actor,
            source_schema="snotes-markdown-v1",
            generated_at="2026-09-29T00:00:00+08:00",
        )
        return output.read_bytes(), result["hash"]

    def _old_current(self, actor: str) -> Path:
        current = self.root / actor / "current.db"
        current.write_bytes(b"OLD_CURRENT")
        os.chmod(current, 0o640)
        return current

    @staticmethod
    def _request(actor: str, payload: bytes, *, suffix: bytes = b"") -> BytesIO:
        digest = hashlib.sha256(payload).hexdigest()
        return BytesIO(
            CloudMirrorReceiverTest._raw_request(
                {"actor": actor, "size": len(payload), "sha256": digest},
                payload + suffix,
            )
        )

    @staticmethod
    def _raw_request(header: dict[str, object], payload: bytes) -> bytes:
        return json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n" + payload


if __name__ == "__main__":
    unittest.main()
