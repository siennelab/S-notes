from __future__ import annotations

from pathlib import Path

from src.app_paths import DATA_ROOT_OVERRIDDEN, DATABASE_PATH, USER_DATA_ROOT
from src.database import create_gale_submission, initialize_database


FICTIONAL_SUBMISSIONS = (
    {
        "external_submission_id": "FICTIONAL_GALE_SUBMISSION_001",
        "idempotency_key": "fictional-gale-r1b-key-001",
        "title": "虚构投稿：岛屿气象观察",
        "body": "A fictional Gale submission for R1B local inbox testing.",
        "main_category": "虚构测试",
        "sub_category": "Gale 投稿",
        "sub_sub_category": "待确认",
        "tags": ["虚构", "R1B"],
    },
    {
        "external_submission_id": "FICTIONAL_GALE_SUBMISSION_002",
        "idempotency_key": "fictional-gale-r1b-key-002",
        "title": "虚构投稿：纸船目录提议",
        "body": "此记录只用于临时数据根中的接收、接受与丢弃验收。",
        "main_category": "虚构测试",
        "sub_category": "Gale 投稿",
        "sub_sub_category": "待确认",
        "tags": ["虚构", "投稿箱"],
    },
)


def _require_isolated_data_root() -> Path:
    root = USER_DATA_ROOT.resolve()
    temporary_root = Path("/private/tmp").resolve()
    if not DATA_ROOT_OVERRIDDEN:
        raise SystemExit("拒绝执行：必须显式设置 S_NOTES_DATA_ROOT。")
    if temporary_root not in root.parents:
        raise SystemExit("拒绝执行：S_NOTES_DATA_ROOT 必须位于 /private/tmp。")
    if not root.name.startswith("snotes-r1b-"):
        raise SystemExit("拒绝执行：临时数据根名称必须以 snotes-r1b- 开头。")
    return root


def main() -> int:
    data_root = _require_isolated_data_root()
    initialize_database(DATABASE_PATH)
    submissions = [
        create_gale_submission(database_path=DATABASE_PATH, **record)
        for record in FICTIONAL_SUBMISSIONS
    ]
    print(f"FICTIONAL_DATA_ROOT={data_root}")
    print(f"FICTIONAL_SUBMISSION_COUNT={len(submissions)}")
    for submission in submissions:
        print(
            "FICTIONAL_SUBMISSION="
            f"{submission['external_submission_id']}:{submission['status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
