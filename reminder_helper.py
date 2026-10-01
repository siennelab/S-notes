from __future__ import annotations

import sys

from src.app_paths import USER_DATA_ROOT
from src.snotes.background.reminder import run_one_shot
from src.snotes.background.reminder import HELPER_VERSION
from src.snotes.reminder_authority import (
    ReminderAuthorityError,
    resolve_reminder_authority,
)
from src.snotes.application.reminder_service import ReminderService
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_ntfy import NtfyReminderTransport


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print(f"snotes-reminder-helper {HELPER_VERSION}")
        return 0
    # The legacy scheduler only ever runs for an explicitly local authority; it
    # must not become a second scheduler next to the Cloud one.
    try:
        authority = resolve_reminder_authority()
    except ReminderAuthorityError as error:
        print(f"snotes-reminder-helper: {error}", file=sys.stderr)
        return 2
    if authority.is_remote:
        print("snotes-reminder-helper: Reminder authority is remote", file=sys.stderr)
        return 2
    return run_one_shot(
        compose_reminder_service(),
        scheduled="--scheduled" in args,
    )


def compose_reminder_service() -> ReminderService:
    connections = initialize_database(USER_DATA_ROOT / "s_notes.db")
    repository = SQLiteReminderRepository(connections)
    settings = repository.settings()
    return ReminderService(
        repository,
        transport=NtfyReminderTransport(settings.ntfy_endpoint or "https://ntfy.sh"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
