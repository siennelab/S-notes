from __future__ import annotations

import io
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import gc35_step07_provision as provision


class LocalReminderProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='snotes-local-provision-')
        self.home = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'HOME': str(self.home)}, clear=True)
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    @property
    def credential_root(self) -> Path:
        return self.home / provision.ACTOR_CREDENTIAL_ROOT

    def _forbid_gui_and_keychain(self):
        return (
            patch.object(provision, 'QApplication', side_effect=AssertionError('GUI used')),
            patch.object(provision, 'owner_credential', side_effect=AssertionError('Keychain used')),
        )

    def test_local_mode_fetches_only_local_namespace_and_writes_private_files(self):
        root = self.credential_root
        root.mkdir(parents=True, mode=0o700)
        originals = {'yan': 'existing yan value\n', 'claude-code': 'existing claude value\n'}
        for actor, value in originals.items():
            (root / actor).write_text(value)

        tokens = {'clawbot': 'C' * 48, 'antigravity': 'A' * 48}

        def ssh_result(command, **kwargs):
            invocation = command[-1]
            actor = next(actor for actor in provision.LOCAL_ACTORS if repr(actor) in invocation)
            return subprocess.CompletedProcess(command, 0, tokens[actor].encode() + b'\n', b'')

        output = io.StringIO()
        gui, keychain = self._forbid_gui_and_keychain()
        with gui, keychain, patch.object(provision.subprocess, 'run', side_effect=ssh_result) as run:
            with redirect_stdout(output):
                result = provision.main(['--local-actors'])

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), 'STEP07_PRIVATE_CREDENTIAL_PROVISION=PASS\n')
        self.assertEqual(run.call_count, 2)
        for actor, call in zip(provision.LOCAL_ACTORS, run.call_args_list, strict=True):
            argv = call.args[0]
            self.assertEqual(argv[:2], ['ssh', '-o'])
            self.assertIn('optimal', argv)
            self.assertIn('local_actor_credentials', argv[-1])
            self.assertIn(actor, argv[-1])
            self.assertTrue(call.kwargs['capture_output'])
            self.assertNotIn('env', call.kwargs)
            self.assertNotIn(tokens[actor], repr(call))

        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        for actor, token in tokens.items():
            target = root / actor
            self.assertEqual(target.read_text(), token + '\n')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        for actor, value in originals.items():
            self.assertEqual((root / actor).read_text(), value)

    def test_failed_second_fetch_writes_nothing_and_reports_no_details(self):
        root = self.credential_root
        root.mkdir(parents=True, mode=0o700)
        existing = {
            'clawbot': 'old local clawbot\n',
            'antigravity': 'old local antigravity\n',
            'yan': 'old yan\n',
            'claude-code': 'old claude\n',
        }
        for actor, value in existing.items():
            (root / actor).write_text(value)

        valid = 'V' * 48
        private_diagnostic = ('D' * 48).encode()
        responses = [
            subprocess.CompletedProcess([], 0, valid.encode(), b''),
            subprocess.CompletedProcess([], 1, private_diagnostic, private_diagnostic),
        ]
        output = io.StringIO()
        gui, keychain = self._forbid_gui_and_keychain()
        with gui, keychain, patch.object(provision.subprocess, 'run', side_effect=responses):
            with redirect_stdout(output):
                result = provision.main(['--local-actors'])

        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), 'STEP07_PRIVATE_CREDENTIAL_PROVISION=FAIL\n')
        self.assertNotIn(valid, output.getvalue())
        self.assertNotIn(private_diagnostic.decode(), output.getvalue())
        for actor, value in existing.items():
            self.assertEqual((root / actor).read_text(), value)

    def test_local_mode_rejects_symlink_target_without_following_it(self):
        root = self.credential_root
        root.mkdir(parents=True, mode=0o700)
        outside = self.home / 'outside'
        outside.write_text('must remain unchanged\n')
        (root / 'clawbot').symlink_to(outside)

        tokens = iter(('C' * 48, 'A' * 48))
        output = io.StringIO()
        gui, keychain = self._forbid_gui_and_keychain()
        with gui, keychain, patch.object(
            provision.subprocess,
            'run',
            side_effect=lambda command, **kwargs: subprocess.CompletedProcess(
                command, 0, next(tokens).encode(), b''
            ),
        ):
            with redirect_stdout(output):
                result = provision.main(['--local-actors'])

        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), 'STEP07_PRIVATE_CREDENTIAL_PROVISION=FAIL\n')
        self.assertEqual(outside.read_text(), 'must remain unchanged\n')
        self.assertTrue((root / 'clawbot').is_symlink())
        self.assertFalse((root / 'antigravity').exists())

    def test_local_mode_rejects_non_private_or_symlinked_directory_before_ssh(self):
        root = self.credential_root
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o755)

        output = io.StringIO()
        with patch.object(provision.subprocess, 'run') as run, redirect_stdout(output):
            result = provision.main(['--local-actors'])
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), 'STEP07_PRIVATE_CREDENTIAL_PROVISION=FAIL\n')
        run.assert_not_called()

        root.rmdir()
        outside = self.home / 'private-outside'
        outside.mkdir(mode=0o700)
        root.symlink_to(outside, target_is_directory=True)
        output = io.StringIO()
        with patch.object(provision.subprocess, 'run') as run, redirect_stdout(output):
            result = provision.main(['--local-actors'])
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), 'STEP07_PRIVATE_CREDENTIAL_PROVISION=FAIL\n')
        run.assert_not_called()

    def test_fetch_allows_only_the_two_fixed_identity_sets(self):
        token = 'T' * 48
        with patch.object(
            provision.subprocess,
            'run',
            return_value=subprocess.CompletedProcess([], 0, token.encode(), b''),
        ) as run:
            for actor in ('owner', 'yan', 'claude-code'):
                self.assertEqual(provision.fetch(actor), token)
                self.assertNotIn('local_actor_credentials', run.call_args.args[0][-1])
                self.assertIn(actor, run.call_args.args[0][-1])
            self.assertEqual(provision.fetch('clawbot', local_actor=True), token)
            self.assertIn('local_actor_credentials', run.call_args.args[0][-1])
            self.assertIn('clawbot', run.call_args.args[0][-1])
            with self.assertRaisesRegex(RuntimeError, 'unsupported_identity'):
                provision.fetch('owner', local_actor=True)
            with self.assertRaisesRegex(RuntimeError, 'unsupported_identity'):
                provision.fetch('clawbot')
            with self.assertRaisesRegex(RuntimeError, 'unsupported_identity'):
                provision.fetch('../other', local_actor=True)
        self.assertEqual(run.call_count, 4)


if __name__ == '__main__':
    unittest.main()
