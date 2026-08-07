from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


MANIFEST_NAME = "r1-safety-acceptance.json"
ROOT_PREFIX = "snotes-r1-safety-acceptance."


def _validated_data_root(raw_path: str) -> Path:
    data_root = Path(raw_path).expanduser().resolve()
    if not data_root.is_absolute() or data_root.name != "data":
        raise ValueError("验收数据目录必须是绝对路径，且末级目录名必须为 data。")
    if not data_root.parent.name.startswith(ROOT_PREFIX):
        raise ValueError(f"验收目录父级名称必须以 {ROOT_PREFIX} 开头。")
    allowed_prefixes = ("/private/tmp/", "/private/var/folders/")
    if not str(data_root).startswith(allowed_prefixes):
        raise ValueError("验收数据目录必须位于 macOS 临时目录。")
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    data_root.chmod(0o700)
    return data_root


def _load_manifest(data_root: Path) -> dict:
    manifest_path = data_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError("尚未准备验收数据；请先运行 prepare。")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _database_api(data_root: Path):
    os.environ["S_NOTES_DATA_ROOT"] = str(data_root)
    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    from src import database

    return database


def prepare(data_root: Path) -> dict:
    database = _database_api(data_root)
    manifest_path = data_root / MANIFEST_NAME
    if manifest_path.exists():
        return _load_manifest(data_root)
    if database.DATABASE_PATH.exists():
        raise ValueError("验收数据库已存在但缺少 manifest；拒绝覆盖。")

    database.initialize_database()
    conflict_note = database.create_note()
    conflict_note = database.update_note(
        conflict_note["id"],
        title="R1验收-保存冲突",
        content_html="<p>虚构初始正文：海风编号 17。</p>",
    )

    accepted_receipt = database.create_gale_submission(
        external_submission_id="FICTIONAL_R1_ACCEPTED_LINKED",
        idempotency_key="fictional-r1-accepted-linked",
        title="R1验收-已入库不可删除",
        body="虚构回执正文：蓝色纸船。",
        main_category="临时",
        sub_category="无",
        sub_sub_category="无",
    )
    accepted = database.accept_ai_receipt_inbox_item(
        accepted_receipt["id"], accepted_receipt
    )

    deletable_receipt = database.create_gale_submission(
        external_submission_id="FICTIONAL_R1_DELETABLE",
        idempotency_key="fictional-r1-deletable",
        title="R1验收-普通已处理可删除",
        body="虚构回执正文：绿色风筝。",
        main_category="临时",
        sub_category="无",
        sub_sub_category="无",
    )
    deletable = database.reject_ai_receipt_inbox_item(deletable_receipt["id"])

    manifest = {
        "conflict_note_id": int(conflict_note["id"]),
        "accepted_receipt_id": int(accepted["receipt"]["id"]),
        "accepted_note_id": int(accepted["note"]["id"]),
        "deletable_receipt_id": int(deletable["id"]),
    }
    temporary_path = manifest_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(manifest_path)
    return manifest


def create_conflict(data_root: Path) -> dict:
    database = _database_api(data_root)
    manifest = _load_manifest(data_root)
    note_id = int(manifest["conflict_note_id"])
    current = database.get_note(note_id)
    if current is None:
        raise ValueError("虚构冲突笔记不存在。")
    updated = database.update_note(
        note_id,
        title=f"R1验收-其他来源 revision {int(current['revision']) + 1}",
        content_html=(
            "<p>虚构其他来源已保存正文；revision "
            f"{int(current['revision']) + 1}。</p>"
        ),
    )
    return {
        "conflict_note_id": note_id,
        "revision": int(updated["revision"]),
        "title": updated["title"],
    }


def verify(data_root: Path) -> dict:
    database = _database_api(data_root)
    manifest = _load_manifest(data_root)
    conflict_note = database.get_note(int(manifest["conflict_note_id"]))
    accepted_receipt = database.get_ai_receipt_inbox_item(
        int(manifest["accepted_receipt_id"])
    )
    accepted_note = database.get_note(int(manifest["accepted_note_id"]))
    deletable_receipt = database.get_ai_receipt_inbox_item(
        int(manifest["deletable_receipt_id"])
    )
    with sqlite3.connect(database.DATABASE_PATH) as connection:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    return {
        "quick_check": quick_check,
        "conflict_note": {
            "exists": conflict_note is not None,
            "revision": int(conflict_note["revision"]) if conflict_note else None,
            "title": conflict_note["title"] if conflict_note else "",
        },
        "accepted_receipt": {
            "exists": accepted_receipt is not None,
            "status": accepted_receipt["status"] if accepted_receipt else "deleted",
        },
        "accepted_note_exists": accepted_note is not None,
        "deletable_receipt": {
            "exists": deletable_receipt is not None,
            "status": deletable_receipt["status"] if deletable_receipt else "deleted",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and verify isolated fictional R1 safety acceptance data."
    )
    parser.add_argument("action", choices=("prepare", "conflict", "verify"))
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args()
    try:
        data_root = _validated_data_root(args.data_root)
        if args.action == "prepare":
            result = prepare(data_root)
        elif args.action == "conflict":
            result = create_conflict(data_root)
        else:
            result = verify(data_root)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"R1_ACCEPTANCE_ERROR={error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
