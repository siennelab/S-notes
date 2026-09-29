"""Manual and scheduled Mac publisher for GreenCloud actor mirrors."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from src.app_paths import DEFAULT_PATHS
from src.services.actor_mirror_publish_service import (
    DEFAULT_SSH_ACCOUNT,
    DEFAULT_SSH_HOST,
    DEFAULT_SSH_IDENTITY,
    ActorMirrorPublishError,
    ActorMirrorPublisher,
    SshActorMirrorTransport,
)


DEFAULT_MIRROR_DIRECTORY = (
    DEFAULT_PATHS.exports / "greencloud-mirrors"
)
DEFAULT_STATE_PATH = (
    DEFAULT_PATHS.config / "actor_mirror_publish_state.json"
)
DEFAULT_LOCK_PATH = DEFAULT_PATHS.config / "actor_mirror_publish.lock"


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = _build_parser()
    args = parser.parse_args(argv)
    transport = SshActorMirrorTransport(
        host=args.ssh_host,
        account=args.ssh_account,
        identity_file=args.identity,
        timeout_seconds=args.timeout,
    )
    publisher = ActorMirrorPublisher(
        source_database=args.source_db,
        redaction_rules=args.redaction_rules,
        mirror_directory=args.mirror_directory,
        state_path=args.state,
        lock_path=args.lock,
        transport=transport,
    )
    try:
        if args.command == "status":
            _print_status(publisher.status())
            return 0
        results = publisher.publish()
        for result in results:
            prefix = result.actor.upper().replace("-", "_")
            _print_fields(
                {
                    f"{prefix}_STATUS": result.status.upper(),
                    f"{prefix}_HASH": result.content_hash or "NONE",
                    f"{prefix}_NOTE_COUNT": result.note_count,
                    f"{prefix}_REASON": result.reason.upper(),
                }
            )
        return 0 if all(
            result.status in {"published", "unchanged"}
            for result in results
        ) else 2
    except ActorMirrorPublishError as error:
        _print_fields(
            {
                "ACTOR_MIRROR_PUBLISH_STATUS": "FAILED",
                "REASON": error.code.upper(),
            }
        )
        return 2
    except Exception:
        _print_fields(
            {
                "ACTOR_MIRROR_PUBLISH_STATUS": "FAILED",
                "REASON": "UNEXPECTED_FAILURE",
            }
        )
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snotes-actor-mirror-publish")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("publish", "status"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "--source-db", type=Path, default=DEFAULT_PATHS.database
        )
        child.add_argument(
            "--redaction-rules",
            type=Path,
            default=DEFAULT_PATHS.redaction_rules,
        )
        child.add_argument(
            "--mirror-directory",
            type=Path,
            default=DEFAULT_MIRROR_DIRECTORY,
        )
        child.add_argument(
            "--state", type=Path, default=DEFAULT_STATE_PATH
        )
        child.add_argument(
            "--lock", type=Path, default=DEFAULT_LOCK_PATH
        )
        child.add_argument("--ssh-host", default=DEFAULT_SSH_HOST)
        child.add_argument("--ssh-account", default=DEFAULT_SSH_ACCOUNT)
        child.add_argument(
            "--identity", type=Path, default=DEFAULT_SSH_IDENTITY
        )
        child.add_argument("--timeout", type=int, default=120)
    return parser


def _print_status(state: dict[str, object]) -> None:
    actors = state.get("actors", {})
    if not isinstance(actors, dict):
        actors = {}
    for actor in ("claude-code", "clawbot"):
        value = actors.get(actor, {})
        if not isinstance(value, dict):
            value = {}
        prefix = actor.upper().replace("-", "_")
        _print_fields(
            {
                f"{prefix}_LAST_SUCCESS_HASH": value.get(
                    "last_success_hash", "NONE"
                ),
                f"{prefix}_LAST_SUCCESS_TIME": value.get(
                    "last_success_time", "NONE"
                ),
                f"{prefix}_LAST_FAILURE_TIME": value.get(
                    "last_failure_time", "NONE"
                ),
                f"{prefix}_LAST_FAILURE_REASON": value.get(
                    "last_failure_reason", "NONE"
                ),
                f"{prefix}_NEEDS_RETRY": (
                    "YES" if value.get("needs_retry", False) else "NO"
                ),
            }
        )


def _print_fields(values: dict[str, object]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    raise SystemExit(main())
