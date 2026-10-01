"""Cloud Reminder wire contract shared by the server and remote clients.

The wire version is deliberately independent from the storage schema version:
a database migration must not change network compatibility by accident.  Bump
it only when the snapshot/command payload contract itself changes.
"""

from __future__ import annotations


REMINDER_WIRE_SCHEMA_VERSION = 4

REMOTE_ERROR_KINDS = frozenset({"offline", "auth", "schema_mismatch", "server"})

# Whether a failed remote call may still have changed Cloud state.
OUTCOME_FAILED = "failed"
OUTCOME_UNKNOWN = "unknown"
REMOTE_OUTCOMES = frozenset({OUTCOME_FAILED, OUTCOME_UNKNOWN})


class RemoteReminderError(RuntimeError):
    """Stable client error classification for GUI and MCP adapters.

    ``outcome`` is ``failed`` when the request definitely did not change Cloud
    state, and ``unknown`` when a write may have been committed without a
    reliable response.
    """

    def __init__(
        self,
        kind: str,
        *,
        code: str | None = None,
        status: int | None = None,
        outcome: str = OUTCOME_FAILED,
    ) -> None:
        if kind not in REMOTE_ERROR_KINDS:
            raise ValueError("unsupported_remote_reminder_error_kind")
        if outcome not in REMOTE_OUTCOMES:
            raise ValueError("unsupported_remote_reminder_outcome")
        self.kind = kind
        self.code = code
        self.status = status
        self.outcome = outcome
        super().__init__(kind if code is None else f"{kind}:{code}")
