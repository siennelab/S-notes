from __future__ import annotations
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from src.snotes.domain.reminder_remote_contract import REMINDER_WIRE_SCHEMA_VERSION
from src.snotes.ui.reminder_owner import OwnerSnapshotCache, owner_credential
from src.snotes.ui.reminder_legacy_check import legacy_scheduler_detected
from src.snotes.step07_candidate import configure_candidate


class OwnerIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cache_compatible_private_atomic_and_excludes_wrapper_secret(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'gui-only' / 'snapshot.json'
            cache = OwnerSnapshotCache(path)
            view = dict(schema_version=REMINDER_WIRE_SCHEMA_VERSION, snapshot_time='2026-09-30T00:00:00Z',
                        server_time='2026-09-30T00:00:00Z', labels=[], tables=[], items=[],
                        settings={}, badge_count=0, token='fictional-owner-secret')
            cache.save(view)
            self.assertNotIn('fictional-owner-secret', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            saved = cache.load()
            self.assertEqual(saved['snapshot_time'], view['snapshot_time'])
            cache.save({**view, 'schema_version': REMINDER_WIRE_SCHEMA_VERSION + 1})
            self.assertEqual(cache.load(), saved)
            path.write_text(json.dumps({**view, 'schema_version': REMINDER_WIRE_SCHEMA_VERSION + 1}))
            self.assertIsNone(cache.load())
            path.write_text('{broken')
            self.assertIsNone(cache.load())

    def test_cache_rejects_shared_permissions_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / 'snapshot'; p.write_text('{}'); p.chmod(0o644)
            self.assertIsNone(OwnerSnapshotCache(p).load())
            link = Path(root) / 'link'; link.symlink_to(p)
            self.assertIsNone(OwnerSnapshotCache(link).load())

    def test_owner_cannot_be_read_on_main_thread(self):
        with patch('src.snotes.ui.reminder_owner.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'gui_worker'):
                owner_credential()
            run.assert_not_called()

    def test_owner_worker_never_uses_secret_in_argv_or_environment(self):
        token = 'fictional-test-owner-value'
        class Worker(QThread):
            def run(self):
                self.value = owner_credential()
        with patch('src.snotes.ui.reminder_owner.subprocess.run', return_value=subprocess.CompletedProcess([], 0, token.encode(), b'')) as run:
            worker = Worker(); worker.start(); self.assertTrue(worker.wait(1000))
            self.assertEqual(worker.value, token)
            self.assertNotIn(token, repr(run.call_args))
            self.assertNotIn(token, repr(dict(os.environ)))

    def test_legacy_check_is_detection_only(self):
        with tempfile.TemporaryDirectory() as root:
            plist = Path(root) / 'Library/LaunchAgents/com.snotes.reminder-helper.plist'
            plist.parent.mkdir(parents=True); plist.write_bytes(b'fictional plist')
            with patch('src.snotes.ui.reminder_legacy_check.subprocess.run') as run:
                self.assertTrue(legacy_scheduler_detected(Path(root)))
                run.assert_not_called()
            self.assertEqual(plist.read_bytes(), b'fictional plist')
            plist.unlink()
            with patch('src.snotes.ui.reminder_legacy_check.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertTrue(legacy_scheduler_detected(Path(root)))
                self.assertEqual(run.call_args.args[0][1], 'print')

    def test_candidate_forces_isolated_root_without_any_secret(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HOME': home, 'S_NOTES_STEP07_CANDIDATE': '1', 'S_NOTES_DATA_ROOT': '/untrusted/formal'}):
            self.assertTrue(configure_candidate())
            self.assertEqual(os.environ['S_NOTES_DATA_ROOT'], str(Path(home) / 'Library/Application Support/S-notes-Step07'))
            self.assertEqual(os.environ['S_NOTES_REMOTE_REMINDER_ENDPOINT'], 'https://reminder.ke-den.de')
            self.assertEqual(os.environ['HOME'], str(Path(home) / 'Library/Application Support/S-notes-Step07/home'))
            self.assertEqual(os.environ['S_NOTES_STEP07_HOST_HOME'], home)


if __name__ == '__main__':
    unittest.main()
