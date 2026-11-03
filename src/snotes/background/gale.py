"""GUI-independent Package 6 scheduled/one-shot entry helpers."""

from __future__ import annotations

import os
import plistlib
import tempfile
from pathlib import Path

from src.snotes.application.gale_service import GaleControlService
from src.snotes.domain.greencloud import MultiSourcePullResult


HELPER_VERSION = "1"
HELPER_FILENAME = "snotes-gale-production-pull"
LAUNCH_AGENT_LABEL = "com.snotes.gale-production-pull"
LAUNCH_AGENT_INTERVAL_SECONDS = 300


def run_one_shot(control: GaleControlService, *, scheduled: bool = False) -> int:
    try:
        if scheduled:
            pull_result, publish_result = control.scheduled_once()
            if publish_result is not None and publish_result.status == "failed":
                return 3
        else:
            pull_result = control.check_submissions_now()
        if isinstance(pull_result, MultiSourcePullResult) and pull_result.failures:
            return 2
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
    """Write an isolated/installable plist; loading remains an operator action."""
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
