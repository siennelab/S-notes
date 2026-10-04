"""Provision the authorized Step 7 candidate without exposing credentials.

Run with the project Python as a GUI utility. Only its GUI worker retrieves the
owner credential; secrets never enter arguments, environment, files or output.
Actor credentials are separate private files, never an owner fallback.
"""
from __future__ import annotations
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication
from src.snotes.ui.reminder_owner import OWNER_KEYCHAIN_ACCOUNT, OWNER_KEYCHAIN_SERVICE, owner_credential


PRIMARY_ACTORS = frozenset({'owner', 'yan', 'claude-code'})
LOCAL_ACTORS = ('clawbot', 'antigravity')
ACTOR_CREDENTIAL_ROOT = Path('Library/Application Support/S-notes-Step07/actor-credentials')


def fetch(actor: str, *, local_actor: bool = False) -> str:
    permitted = LOCAL_ACTORS if local_actor else PRIMARY_ACTORS
    if actor not in permitted:
        raise RuntimeError('unsupported_identity')
    selection = (
        "['local_actor_credentials'][" + repr(actor) + "]"
        if local_actor else "[" + repr(actor) + "]"
    )
    code = ("import json; from pathlib import Path; "
            "print(json.loads(Path('/root/greencloud-secrets/reminder-api.json').read_text())"
            + selection + ")")
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


def _credential_root() -> Path:
    root = Path.home() / ACTOR_CREDENTIAL_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = root.lstat()
    if stat.S_ISLNK(details.st_mode):
        raise RuntimeError('credential_directory_symlink')
    if not stat.S_ISDIR(details.st_mode):
        raise RuntimeError('credential_directory_invalid')
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        raise RuntimeError('credential_directory_permissions')
    return root


def _write_actor_credential(root: Path, actor: str, value: str) -> None:
    target = root / actor
    if target.is_symlink():
        raise RuntimeError('credential_file_symlink')
    fd, name = tempfile.mkstemp(prefix='.credential-', dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def provision_local_actors() -> None:
    root = _credential_root()
    values = {actor: fetch(actor, local_actor=True) for actor in LOCAL_ACTORS}
    for actor in LOCAL_ACTORS:
        _write_actor_credential(root, actor, values.pop(actor))


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
            root = _credential_root()
            for actor in ('yan', 'claude-code'):
                value = fetch(actor)
                _write_actor_credential(root, actor, value)
                del value
            self.done.emit(True)
        except Exception:
            # Deliberately do not include subprocess output or exception text.
            self.done.emit(False)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if '--local-actors' in args:
        ok = False
        if args == ['--local-actors']:
            try:
                provision_local_actors()
                ok = True
            except Exception:
                pass
        print('STEP07_PRIVATE_CREDENTIAL_PROVISION=' + ('PASS' if ok else 'FAIL'))
        return 0 if ok else 1

    app_args = sys.argv if argv is None else [sys.argv[0], *args]
    app = QApplication(app_args)
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
