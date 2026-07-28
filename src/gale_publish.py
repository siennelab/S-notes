from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from src.app_paths import DEFAULT_PATHS
from src.services.gale_publish_service import (
    GalePublishConfig,
    GalePublishError,
    activate_remote_mirror,
    load_publish_state,
    operator_rollback_remote_mirror,
    production_remote_activation_config,
    publish_gale_mirror,
)

DEFAULT_MIRROR_PATH = DEFAULT_PATHS.exports / "gale" / "gale-mirror.db"
DEFAULT_STATE_PATH = DEFAULT_PATHS.config / "gale_publish_state.json"
DEFAULT_LOCK_PATH = DEFAULT_PATHS.config / "gale_publish.lock"


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            result = publish_gale_mirror(
                GalePublishConfig(
                    mirror_path=args.mirror,
                    state_path=args.state,
                    lock_path=args.lock,
                )
            )
            _print_fields(
                {
                    "GALE_PUBLISH_STATUS": result.status.upper(),
                    "SHA256": result.sha256,
                    "NOTE_COUNT": result.note_count,
                    "REASON": result.reason,
                    "PREVIOUS_PRESERVED": _yes_no(
                        result.previous_preserved
                    ),
                }
            )
            return 0 if result.status in {"success", "duplicate"} else 2

        if args.command == "status":
            state = load_publish_state(args.state)
            _print_fields(
                {
                    "GALE_PUBLISH_STATUS": "STATE",
                    "IS_PUBLISHING": _yes_no(state.is_publishing),
                    "LAST_SUCCESS_TIME": state.last_success_time or "NONE",
                    "LAST_SUCCESS_HASH": state.last_success_hash or "NONE",
                    "LAST_FAILURE_TIME": state.last_failure_time or "NONE",
                    "LAST_FAILURE_REASON": (
                        state.last_failure_reason or "NONE"
                    ),
                    "NEEDS_RETRY": _yes_no(state.needs_retry),
                }
            )
            return 0

        if os.geteuid() != 0:
            raise GalePublishError(
                "remote_privilege_required",
                previous_preserved=False,
            )
        if args.command == "remote-rollback":
            rollback = operator_rollback_remote_mirror(
                production_remote_activation_config(),
                expected_current_sha256=args.expected_current_sha256,
                expected_previous_sha256=args.expected_previous_sha256,
            )
            _print_fields(
                {
                    "GALE_ROLLBACK_STATUS": rollback.status.upper(),
                    "REASON": rollback.reason,
                    "CURRENT_SHA256": rollback.current_sha256,
                    "CURRENT_NOTE_COUNT": rollback.current_note_count,
                    "PREVIOUS_SHA256": rollback.previous_sha256,
                    "PREVIOUS_NOTE_COUNT": rollback.previous_note_count,
                    "AUTO_RESTORED": _yes_no(rollback.auto_restored),
                }
            )
            return _operator_rollback_exit_code(
                rollback.reason,
                rollback.status,
            )
        activation = activate_remote_mirror(
            production_remote_activation_config(),
            expected_sha256=args.expected_sha256,
            expected_note_count=args.expected_note_count,
        )
        _print_fields(
            {
                "GALE_ACTIVATE_STATUS": activation.status.upper(),
                "REASON": activation.reason,
                "SHA256": activation.sha256,
                "NOTE_COUNT": activation.note_count,
                "ROLLED_BACK": _yes_no(activation.rolled_back),
                "PREVIOUS_PRESERVED": _yes_no(
                    activation.previous_preserved
                ),
            }
        )
        return _activation_exit_code(activation.reason, activation.status)
    except GalePublishError as error:
        if args.command == "remote-rollback":
            _print_operator_rollback_failure(error.code)
        else:
            _print_failure(error.code, error.previous_preserved)
        return 2
    except Exception:
        if args.command == "remote-rollback":
            _print_operator_rollback_failure("unexpected_failure")
        else:
            _print_failure("unexpected_failure", False)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snotes-gale-publish")
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument(
        "--mirror",
        type=Path,
        default=DEFAULT_MIRROR_PATH,
    )
    publish_parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_PATH,
    )
    publish_parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_PATH,
    )

    activate_parser = subparsers.add_parser(
        "remote-activate",
        help=argparse.SUPPRESS,
    )
    activate_parser.add_argument("--expected-sha256", required=True)
    activate_parser.add_argument(
        "--expected-note-count",
        required=True,
        type=int,
    )
    rollback_parser = subparsers.add_parser(
        "remote-rollback",
        help=argparse.SUPPRESS,
    )
    rollback_parser.add_argument(
        "--expected-current-sha256",
        required=True,
    )
    rollback_parser.add_argument(
        "--expected-previous-sha256",
        required=True,
    )
    return parser


def _activation_exit_code(reason: str, status: str) -> int:
    if status == "success":
        return 0
    if status == "duplicate":
        return 3
    if reason == "publisher_busy":
        return 10
    if reason == "remote_validation_failed":
        return 20
    if reason == "activation_failed_rolled_back":
        return 30
    if reason == "rollback_failed":
        return 31
    return 20


def _operator_rollback_exit_code(reason: str, status: str) -> int:
    if status == "success":
        return 0
    if reason == "publisher_busy":
        return 10
    if reason in {
        "health_check_failed_restored",
        "service_stop_failed",
        "swap_failed_restored",
    }:
        return 30
    if reason == "rollback_failed":
        return 31
    return 20


def _print_failure(reason: str, previous_preserved: bool) -> None:
    _print_fields(
        {
            "GALE_PUBLISH_STATUS": "FAILED",
            "REASON": reason,
            "PREVIOUS_PRESERVED": _yes_no(previous_preserved),
        },
        stream=sys.stderr,
    )


def _print_operator_rollback_failure(reason: str) -> None:
    _print_fields(
        {
            "GALE_ROLLBACK_STATUS": "FAILED",
            "REASON": reason,
        },
        stream=sys.stderr,
    )


def _print_fields(
    fields: dict[str, object],
    *,
    stream=sys.stdout,
) -> None:
    for key, value in fields.items():
        stream.write(f"{key}={value}\n")


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


if __name__ == "__main__":
    raise SystemExit(main())
