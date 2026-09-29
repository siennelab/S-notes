#!/usr/bin/env python3
"""Build fictional v1/v2 actor mirrors through the production Step 4 service."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.snotes.application.actor_mirror_service import ActorMirrorService
from src.snotes.application.ai_actor_registry import AI_TARGET_REGISTRY
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.actor_mirror import SQLiteActorMirrorWriter
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database


ACTORS = ("claude-code", "clawbot")
RAW_TOKEN = "FICTIONAL_STEP05_RAW_SECRET_TOKEN_7B9F"
REDACTION_TOKEN = "FICTIONAL_REDACT_ME_4C2A"
REDACTED_TEXT = "Fictional redacted note: [REDACTED]."
LOCATION = NoteLocation("Fictional", "Step5")
TARGET_CATALOG = tuple(
    (item.target_id, item.display_name, item.slug, item.selectable, item.system)
    for item in AI_TARGET_REGISTRY
)


def build_fixtures(output_root: Path) -> dict[str, object]:
    root = Path(output_root).absolute()
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise RuntimeError("output_root_must_be_empty_directory")
    else:
        root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)

    with tempfile.TemporaryDirectory(prefix="snotes-step05-source-") as source_name:
        source_root = Path(source_name)
        source_database = source_root / "fictional-source.db"
        rules = source_root / "fictional-redaction-rules.json"
        rules.write_text(
            json.dumps({REDACTION_TOKEN: "[REDACTED]", RAW_TOKEN: "[FULL_MUST_NOT_REDACT]"}),
            encoding="utf-8",
        )
        connections = initialize_database(source_database)
        directories = SQLiteDirectoriesRepository(connections)
        notes = SQLiteNotesRepository(connections, directories)
        service = ActorMirrorService(
            notes,
            AiSafeReadService(rules, body_renderer=markdown_to_plain_text),
            SQLiteActorMirrorWriter(),
        )
        ids = {
            "common_full": _create_note(
                notes,
                title="Fictional common full",
                markdown=_common_markdown("v1"),
                visibility="full",
                targets=("common",),
            ),
            "claude_only": _create_note(
                notes,
                title="Fictional Claude only",
                markdown="# Claude only\n\nFictional Claude comet",
                visibility="full",
                targets=("claude-code",),
            ),
            "clawbot_only": _create_note(
                notes,
                title="Fictional Clawbot only",
                markdown="# Clawbot only\n\nFictional Clawbot comet",
                visibility="full",
                targets=("clawbot",),
            ),
            "hidden": _create_note(
                notes,
                title="Fictional hidden",
                markdown="Hidden fixture must never enter a mirror",
                visibility="hidden",
                targets=("common",),
            ),
            "unauthorized_gale_only": _create_note(
                notes,
                title="Fictional Gale only",
                markdown="Gale-only fixture must never enter either actor mirror",
                visibility="full",
                targets=("gale",),
            ),
            "redacted": _create_note(
                notes,
                title="Fictional redacted",
                markdown=f"Fictional redacted note: {REDACTION_TOKEN}.",
                visibility="redacted",
                targets=("common",),
            ),
        }
        manifest: dict[str, object] = {
            "fixture": "gc35-step05-fictional-v1-v2",
            "actors": list(ACTORS),
            "ids": ids,
            "expectations": {
                "raw_token": RAW_TOKEN,
                "redacted_text": REDACTED_TEXT,
                "dialect": "snotes-markdown-v1",
                "versions": {},
            },
            "versions": {},
            "source_database_uploaded": False,
        }
        _generate_version(root, manifest, notes, service, version="v1")

        common = notes.get(ids["common_full"])
        if common is None:
            raise RuntimeError("fictional_common_missing")
        notes.save(
            common.id,
            title=common.title,
            content_markdown=_common_markdown("v2"),
            location=LOCATION,
            tags=("fictional", "step5"),
            expected_revision=common.revision,
        )
        _generate_version(root, manifest, notes, service, version="v2")
        # Leaving this context removes the temporary initialized source DB and rules.

    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    return manifest


def _generate_version(
    root: Path,
    manifest: dict[str, object],
    notes: SQLiteNotesRepository,
    service: ActorMirrorService,
    *,
    version: str,
) -> None:
    version_root = root / version
    version_root.mkdir(mode=0o700)
    version_records: dict[str, object] = {}
    versions = manifest["versions"]
    assert isinstance(versions, dict)
    versions[version] = version_records
    for actor in ACTORS:
        output = version_root / f"{actor}.db"
        result = service.generate(actor, output)
        version_records[actor] = {
            "relative_path": str(output.relative_to(root)),
            "file_sha256": _sha256(output),
            "content_hash": result["hash"],
            "note_count": result["note_count"],
        }

    ids = manifest["ids"]
    expectations = manifest["expectations"]
    assert isinstance(ids, dict) and isinstance(expectations, dict)
    expected_versions = expectations["versions"]
    assert isinstance(expected_versions, dict)
    common = notes.get(int(ids["common_full"]))
    redacted = notes.get(int(ids["redacted"]))
    claude = notes.get(int(ids["claude_only"]))
    clawbot = notes.get(int(ids["clawbot_only"]))
    if any(note is None for note in (common, redacted, claude, clawbot)):
        raise RuntimeError("fictional_note_missing")
    assert common is not None and redacted is not None
    assert claude is not None and clawbot is not None
    expected_versions[version] = {
        "common": {
            "safe_text": markdown_to_plain_text(common.content_markdown),
            "markdown": common.content_markdown,
            "version": note_version_token(common.id, common.updated_at),
        },
        "redacted": {
            "safe_text": REDACTED_TEXT,
            "version": note_version_token(redacted.id, redacted.updated_at),
        },
        "claude_only": {
            "safe_text": markdown_to_plain_text(claude.content_markdown),
            "version": note_version_token(claude.id, claude.updated_at),
        },
        "clawbot_only": {
            "safe_text": markdown_to_plain_text(clawbot.content_markdown),
            "version": note_version_token(clawbot.id, clawbot.updated_at),
        },
    }


def _create_note(
    notes: SQLiteNotesRepository,
    *,
    title: str,
    markdown: str,
    visibility: str,
    targets: tuple[str, ...],
) -> int:
    created = notes.create(LOCATION)
    saved = notes.save(
        created.id,
        title=title,
        content_markdown=markdown,
        location=LOCATION,
        tags=("fictional", "step5"),
        expected_revision=created.revision,
    )
    notes.set_ai_visibility(saved.id, visibility)
    notes.set_ai_targets(saved.id, targets, TARGET_CATALOG)
    return saved.id


def _common_markdown(version: str) -> str:
    suffix = "\n\nChanged in v2." if version == "v2" else ""
    return f"# Common full\n\nRaw `{RAW_TOKEN}`{suffix}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build_fixtures(args.output_root)
    except Exception as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
        return 1
    versions = manifest["versions"]
    assert isinstance(versions, dict)
    print(
        json.dumps(
            {
                "status": "ok",
                "fixture": manifest["fixture"],
                "version_count": len(versions),
                "actor_count": len(ACTORS),
                "mirror_count": sum(len(value) for value in versions.values()),
                "source_database_uploaded": False,
                "source_database_retained": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
