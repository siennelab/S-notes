"""Fixed-DB Cloud side of the existing narrow Mac SSH transport."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, "/srv/snotes/runtime")
from src.services.gale_submission_outbox import (
    GaleSubmissionOutboxError,
    acknowledge_submission,
    export_unacknowledged,
    record_terminal_receipt,
)

DATABASE = Path("/srv/snotes/data/outbox.db")
ACTORS = frozenset({"claude-code", "clawbot"})
MAX_REQUEST_BYTES = 512 * 1024


def dispatch(action: str, request: dict, *, database: Path = DATABASE) -> dict:
    if not isinstance(request, dict):
        raise ValueError("request_invalid")
    if action == "export":
        if set(request) - {"limit"}:
            raise ValueError("request_invalid")
        limit = request.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_export_limit")
        submissions = export_unacknowledged(database, limit=limit)
        return {"status": "ok", "source": "greencloud", "schema_version": 3,
                "submissions": [item.operation_transport_payload() for item in submissions]}
    if action == "acknowledge":
        if set(request) != {"submission_uuid", "local_receipt_id"}:
            raise ValueError("request_invalid")
        if any(not isinstance(value, str) for value in request.values()):
            raise ValueError("request_invalid")
        submission, duplicate = acknowledge_submission(database, **request)
    elif action == "receipt":
        required = {"source_actor", "request_id", "terminal_state"}
        if not required <= set(request) or set(request) - required - {"receipt_payload"}:
            raise ValueError("request_invalid")
        if request["source_actor"] not in ACTORS:
            raise ValueError("actor_invalid")
        if any(not isinstance(request[key], str) for key in required):
            raise ValueError("request_invalid")
        if "receipt_payload" in request and not isinstance(request["receipt_payload"], dict):
            raise ValueError("request_invalid")
        submission, duplicate = record_terminal_receipt(database, **request)
    else:
        raise ValueError("command_rejected")
    return {"status": "ok", **submission.operation_status_payload(duplicate=duplicate)}


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("command_rejected")
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        result = dispatch(sys.argv[1], json.loads(raw or b"{}"))
    except (GaleSubmissionOutboxError, ValueError) as error:
        reason = error.code if isinstance(error, GaleSubmissionOutboxError) else "transport_request_rejected"
        print(json.dumps({"status": "failed", "reason": reason}))
        return 2
    except Exception:
        print('{"status":"failed","reason":"outbox_transport_unavailable"}')
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
