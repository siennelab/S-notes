"""Explicit development-only configuration before any application imports."""
from __future__ import annotations
import os
import plistlib
import sys
from pathlib import Path

STAGING_ENDPOINT = 'https://reminder.ke-den.de'


def configure_candidate() -> bool:
    candidate = os.environ.get('S_NOTES_STEP07_CANDIDATE') == '1'
    if getattr(sys, 'frozen', False):
        info = Path(sys.executable).parent.parent / 'Info.plist'
        try:
            candidate = candidate or plistlib.loads(info.read_bytes()).get('SNotesStep07Candidate') is True
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
    if not candidate:
        return False
    # A Finder launch and MCP entry both get the same isolated development root.
    # Never honor an inherited formal data root in this candidate.
    host_home = Path(os.environ.get('S_NOTES_STEP07_HOST_HOME', str(Path.home())))
    root = host_home / 'Library/Application Support/S-notes-Step07'
    isolated_home = root / 'home'
    isolated_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or isolated_home.is_symlink():
        raise RuntimeError('step07_candidate_symlink_rejected')
    os.environ['S_NOTES_STEP07_HOST_HOME'] = str(host_home)
    os.environ['HOME'] = str(isolated_home)
    os.environ['S_NOTES_DATA_ROOT'] = str(root)
    # Explicit Step 7 staging authority; a packaged candidate's Info.plist
    # declares the same values, and the resolver rejects any mismatch.
    os.environ['S_NOTES_REMINDER_AUTHORITY'] = 'remote'
    os.environ['S_NOTES_REMOTE_REMINDER_ENDPOINT'] = STAGING_ENDPOINT
    os.environ['S_NOTES_STEP07_CANDIDATE'] = '1'
    return True
