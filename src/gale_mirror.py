from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.services.gale_mirror_service import (
    GALE_ACTOR_ID,
    MIRROR_SCHEMA_VERSION,
    SEMANTIC_SEARCH_INCLUDED,
    GaleMirrorError,
    export_gale_mirror,
    verify_gale_mirror,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_gale_mirror(
                source_database=args.source_db,
                output_path=args.output,
                redaction_rules_path=args.redaction_rules,
                actor=args.actor,
            )
            _print_fields(
                {
                    "MIRROR_EXPORT_STATUS": "PASS",
                    "TARGET_ACTOR": result.target_actor,
                    "NOTE_COUNT": result.note_count,
                    "FULL_NOTE_COUNT": result.full_note_count,
                    "REDACTED_NOTE_COUNT": result.redacted_note_count,
                    "OUTPUT": result.output_path,
                    "SCHEMA_VERSION": result.schema_version,
                    "SHA256": result.sha256,
                    "FILE_MODE": result.file_mode,
                    "SEMANTIC_SEARCH_INCLUDED": _yes_no(
                        SEMANTIC_SEARCH_INCLUDED
                    ),
                }
            )
            return 0

        verification = verify_gale_mirror(
            args.mirror,
            expected_actor=args.actor,
        )
        _print_fields(
            {
                "MIRROR_VERIFY_STATUS": "PASS",
                "TARGET_ACTOR": args.actor,
                "NOTE_COUNT": verification.note_count,
                "FTS_COUNT": verification.fts_count,
                "FULL_NOTE_COUNT": verification.full_note_count,
                "REDACTED_NOTE_COUNT": verification.redacted_note_count,
                "SCHEMA_VERSION": MIRROR_SCHEMA_VERSION,
                "FILE_MODE": verification.file_mode,
                "SEMANTIC_SEARCH_INCLUDED": _yes_no(
                    SEMANTIC_SEARCH_INCLUDED
                ),
            }
        )
        return 0
    except GaleMirrorError as error:
        sys.stderr.write(f"GALE_MIRROR_STATUS=FAIL\nERROR_CODE={error.code}\n")
        return 2
    except Exception:
        sys.stderr.write(
            "GALE_MIRROR_STATUS=FAIL\nERROR_CODE=unexpected_failure\n"
        )
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snotes-gale-mirror")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--source-db", type=Path, required=True)
    export_parser.add_argument("--output", type=Path, required=True)
    export_parser.add_argument("--redaction-rules", type=Path, required=True)
    export_parser.add_argument(
        "--actor",
        choices=(GALE_ACTOR_ID,),
        default=GALE_ACTOR_ID,
    )

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--mirror", type=Path, required=True)
    verify_parser.add_argument(
        "--actor",
        choices=(GALE_ACTOR_ID,),
        default=GALE_ACTOR_ID,
    )
    return parser


def _print_fields(fields: dict[str, object]) -> None:
    for key, value in fields.items():
        print(f"{key}={value}")


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


if __name__ == "__main__":
    raise SystemExit(main())
