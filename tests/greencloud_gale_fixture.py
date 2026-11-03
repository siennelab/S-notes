"""Fictional Gale mirrors built with the same Mac actor publisher and receiver."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile

from src.services.actor_mirror_publish_service import ActorMirrorPublisher, PublishReceipt
from src.snotes.domain.greencloud import GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE
from src.snotes.storage.cloud_mirror_receiver import receive_publish
from src.snotes.storage.schema import initialize_database
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from tests.gc35_step05_cloud_fixture import _create_note


class FixtureTransport:
    def __init__(self, mirror_root: Path):
        self.root = mirror_root

    def publish(self, *, actor, mirror_path, file_sha256, content_hash):
        data = mirror_path.read_bytes()
        header = {"actor": actor, "size": len(data), "sha256": file_sha256}
        result = receive_publish(io.BytesIO(json.dumps(header).encode() + b"\n" + data), mirror_root=self.root)
        return PublishReceipt(actor, result["hash"], result["note_count"])


def build_fixtures(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    mirror_root = root / "mirror"
    for actor in GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE:
        (mirror_root / actor).mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="snotes-gale-fictional-source-") as name:
        source = Path(name)
        database = source / "source.db"
        connections = initialize_database(database)
        directories = SQLiteDirectoriesRepository(connections)
        notes = SQLiteNotesRepository(connections, directories)
        specs = (
            ("common", "full", ("common",), "# Fixture\n\nfictional unique body"),
            ("gale", "full", ("gale",), "# Gale\n\nfictional Gale exclusive"),
            ("gale_authorized", "full", ("gale", "claude-code"), "fictional Gale and Claude"),
            ("claude", "full", ("claude-code",), "fictional Claude exclusive"),
            ("clawbot", "full", ("clawbot",), "fictional Clawbot exclusive"),
            ("hidden", "hidden", ("common",), "fictional hidden canary"),
            ("redacted", "redacted", ("common",), "fictional FICTIONAL_REDACT_ME"),
        )
        ids = {key: _create_note(notes, title="FICTIONAL " + key, markdown=body,
                                 visibility=visibility, targets=targets)
               for key, visibility, targets, body in specs}
        rules = source / "rules.json"
        rules.write_text(json.dumps({"FICTIONAL_REDACT_ME": "[REDACTED]"}))
        publisher = ActorMirrorPublisher(source_database=database, redaction_rules=rules,
                                         mirror_directory=source / "exports", state_path=source / "state.json",
                                         lock_path=source / "publish.lock", transport=FixtureTransport(mirror_root))
        results = publisher.publish()
        if not all(result.status == "published" for result in results):
            raise RuntimeError("fictional_publish_failed")
    manifest = {"fixture": "greencloud-gale-internal-fictional", "ids": ids,
                "source_database_uploaded": False,
                "mirrors": {actor: hashlib.sha256((mirror_root / actor / "current.db").read_bytes()).hexdigest()
                            for actor in GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE}}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
