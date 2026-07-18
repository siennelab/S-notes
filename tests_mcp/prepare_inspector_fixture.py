from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.database import initialize_database
from src.services import ai_access_service as service


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a non-private temporary MCP Inspector fixture.")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    database_path = root / "s_notes.db"
    rules_path = root / "redaction_rules.json"
    config_path = root / "config" / "mcp_server.json"
    if database_path.exists():
        raise SystemExit("Refusing to overwrite an existing Inspector database.")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    initialize_database(database_path)
    rules_path.write_text(
        json.dumps({"InspectorSecret": "[REDACTED]"}, ensure_ascii=False),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps({"actor_target_id": "gale"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    created = service.ai_create_note(
        "gale",
        "inspector-base-note",
        title="Inspector visible sample",
        body="Safe temporary sample body.",
        tags=["inspector", "temporary"],
        database_path=database_path,
    )
    note = service.safe_get_note_by_id(
        created["note_id"], "gale", database_path=database_path,
        redaction_rules_path=rules_path,
    )
    service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, True, database_path)
    pending = service.ai_create_note(
        "gale",
        "inspector-pending-create",
        title="Inspector pending sample",
        body="This remains a proposal until a human accepts it.",
        database_path=database_path,
    )
    service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, False, database_path)
    service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, False, database_path)

    print(json.dumps({
        "status": "ok",
        "database_path": str(database_path),
        "redaction_rules_path": str(rules_path),
        "config_path": str(config_path),
        "sample_note_id": created["note_id"],
        "sample_updated_at": note["updated_at"],
        "sample_version": note["version"],
        "pending_request_id": pending["request_id"],
        "pending_proposal_id": pending.get("proposal_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
