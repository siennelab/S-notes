"""GUI-independent Reminder R1 scheduled entry helpers."""

from __future__ import annotations

import os
import plistlib
import tempfile
from pathlib import Path

from src.snotes.application.reminder_service import ReminderService


HELPER_VERSION = "1"
HELPER_FILENAME = "snotes-reminder-helper"
LAUNCH_AGENT_LABEL = "com.snotes.reminder-helper"
LAUNCH_AGENT_INTERVAL_SECONDS = 300


def run_one_shot(service: ReminderService, *, scheduled: bool = False) -> int:
    try:
        if scheduled:
            service.run_scheduled_once()
        else:
            service.dispatch_due_notifications()
        return 0
    except Exception:
        return 2


def desired_launch_agent(helper_path: Path) -> dict:
    resolved = Path(helper_path).resolve()
    if not resolved.is_absolute():
        raise ValueError("helper_path_must_be_absolute")
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(resolved), "--scheduled"],
        "RunAtLoad": True,
        "StartInterval": LAUNCH_AGENT_INTERVAL_SECONDS,
    }


def write_launch_agent_template(destination: Path, helper_path: Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(desired_launch_agent(helper_path), stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, target)
    finally:
        try:
            Path(name).unlink()
        except FileNotFoundError:
            pass
    return target

