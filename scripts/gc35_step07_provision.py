"""Provision the authorized Step 7 candidate without exposing credentials.

Run with the project Python as a GUI utility. Only its GUI worker retrieves the
owner credential; secrets never enter arguments, environment, files or output.
Actor credentials are separate private files, never an owner fallback.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication
from src.snotes.ui.reminder_owner import OWNER_KEYCHAIN_ACCOUNT, OWNER_KEYCHAIN_SERVICE, owner_credential


def fetch(actor: str) -> str:
    if actor not in {'owner', 'yan', 'claude-code'}:
        raise RuntimeError('unsupported_identity')
    code = "import json; from pathlib import Path; print(json.loads(Path('/root/greencloud-secrets/reminder-api.json').read_text())[" + repr(actor) + "])"
    import shlex
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'optimal',
         'python3 -c ' + shlex.quote(code)], capture_output=True, timeout=15,
    )
    if result.returncode:
        raise RuntimeError('staging_credential_transfer_failed')
    value = result.stdout.decode().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', value):
        raise RuntimeError('staging_credential_format_invalid')
    return value


class ProvisionWorker(QThread):
    done = Signal(bool)

    def run(self):
        try:
            owner = fetch('owner')
            # security interactive mode keeps the password out of argv/ps and
            # environment. Capture all output, which may include diagnostics.
            command = (f'add-generic-password -U -s {OWNER_KEYCHAIN_SERVICE} '
                       f'-a {OWNER_KEYCHAIN_ACCOUNT} -w {owner} -T /usr/bin/security\n')
            result = subprocess.run(['/usr/bin/security', '-i'], input=command.encode(),
                                    capture_output=True, timeout=15)
            if result.returncode or owner_credential() != owner:
                raise RuntimeError('keychain_provision_failed')
            del owner, command, result
            root = Path.home() / 'Library/Application Support/S-notes-Step07/actor-credentials'
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if root.is_symlink():
                raise RuntimeError('credential_directory_symlink')
            for actor in ('yan', 'claude-code'):
                value = fetch(actor)
                target = root / actor
                if target.is_symlink():
                    raise RuntimeError('credential_file_symlink')
                fd, name = tempfile.mkstemp(prefix='.credential-', dir=root)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        stream.write(value + '\n')
                    os.replace(name, target)
                finally:
                    Path(name).unlink(missing_ok=True)
                del value
            self.done.emit(True)
        except Exception:
            # Deliberately do not include subprocess output or exception text.
            self.done.emit(False)


def main() -> int:
    app = QApplication(sys.argv)
    result = {'ok': False}
    worker = ProvisionWorker()
    def finished(ok):
        result['ok'] = ok
        app.quit()
    worker.done.connect(finished)
    worker.start()
    app.exec()
    worker.wait()
    print('STEP07_PRIVATE_CREDENTIAL_PROVISION=' + ('PASS' if result['ok'] else 'FAIL'))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
