from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from src.database import (
    _connect,
    _ensure_directory_path,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
    initialize_database,
    list_ai_visible_notes,
    search_notes,
)
from src.search_query import parse_search_query
from src.services.ai_access_service import safe_search_notes
from src.services.gale_mirror_service import export_gale_mirror, search_gale_mirror
from src.services.gale_remote_service import GaleRemoteConfig, search_remote_mirror
from src.time_utils import now_shanghai_iso


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SearchQueryNormalizationTest(unittest.TestCase):
    def test_equivalent_multi_keyword_forms(self) -> None:
        expected = ("tun", "pixel")
        for value in (
            "tun pixel",
            "TUN PIXEL",
            "Pixel，TUN",
            "ＴＵＮ　Ｐｉｘｅｌ",
            "TUN → Pixel",
            "TUN / Pixel",
            "TUN\nPixel",
            "TUN\tPixel",
            "tun     pixel",
            "tun,pixel",
            "tun：pixel",
        ):
            with self.subTest(value=value):
                self.assertEqual(set(parse_search_query(value).tokens), set(expected))

    def test_stable_deduplication_empty_and_punctuation_only(self) -> None:
        self.assertEqual(parse_search_query("Tun tun PIXEL pixel").tokens, ("tun", "pixel"))
        self.assertEqual(parse_search_query("").tokens, ())
        self.assertEqual(parse_search_query("，，，***").tokens, ())

    def test_technical_tokens_are_preserved(self) -> None:
        self.assertEqual(
            parse_search_query(
                "v2rayN wg-hi node.js C++ C# 10.77.77.1 2026-08-03"
            ).tokens,
            (
                "v2rayn",
                "wg-hi",
                "node.js",
                "c++",
                "c#",
                "10.77.77.1",
                "2026-08-03",
            ),
        )


class UnifiedMultiKeywordSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_umask = os.umask(0o077)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "fictional-s-notes.db"
        self.rules_path = self.root / "fictional-redaction-rules.json"
        self.mirror_root = self.root / "mirror"
        self.mirror_root.mkdir()
        self.mirror_path = self.mirror_root / "gale-mirror.db"
        self.outbox_root = self.root / "production-outbox"
        self.outbox_root.mkdir(mode=0o700)
        self.outbox_path = self.outbox_root / "outbox.db"
        initialize_database(self.database_path)
        self.rules_path.write_text(
            json.dumps({"FICTIONAL_RAW_CANARY": "FICTIONAL_SAFE_MASK"}),
            encoding="utf-8",
        )
        self.ids = self._seed_fictional_notes()
        export_gale_mirror(
            source_database=self.database_path,
            output_path=self.mirror_path,
            redaction_rules_path=self.rules_path,
            generator_app_version="fictional-unified-search-test",
        )
        self.remote_config = GaleRemoteConfig(
            mirror_path=self.mirror_path,
            mirror_root=self.mirror_root,
            issuer_url="https://issuer.example.test/",
            resource_url="https://resource.example.test/mcp",
            audience="https://resource.example.test/mcp",
            jwks_url="https://issuer.example.test/.well-known/jwks.json",
            allowed_subjects=frozenset({"fictional-subject"}),
            expected_owner_uid=os.geteuid(),
            outbox_root=self.outbox_root,
            outbox_path=self.outbox_path,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()
        os.umask(self.previous_umask)

    def _create_note(
        self,
        *,
        title: str,
        body: str,
        visibility: str,
        targets: list[str],
        tags: list[str],
        directory: tuple[str, str, str],
        deleted: bool = False,
        legacy_tags: str = "",
        updated_at: str | None = None,
    ) -> int:
        timestamp = updated_at or now_shanghai_iso()
        with _connect(self.database_path) as connection:
            directory_id = _ensure_directory_path(connection, *directory)
            content_html = _plain_text_to_html(body)
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    directory_id, title, body_rich_text, content_html,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, is_pinned, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    directory_id,
                    title,
                    content_html,
                    content_html,
                    directory[0],
                    directory[1],
                    directory[2],
                    legacy_tags,
                    visibility,
                    int(deleted),
                    timestamp,
                    timestamp,
                ),
            )
            note_id = int(cursor.lastrowid)
            _set_note_tags_with_connection(connection, note_id, tags, timestamp)
            if legacy_tags:
                connection.execute(
                    "UPDATE notes SET tags=? WHERE id=?",
                    (legacy_tags, note_id),
                )
            _set_note_ai_targets_with_connection(
                connection,
                note_id,
                targets,
                timestamp,
            )
        return note_id

    def _seed_fictional_notes(self) -> dict[str, int]:
        return {
            "primary": self._create_note(
                title="ALPHA TUN NETWORK",
                body="G有时候很温柔有时候很墨迹有时候很谨慎。",
                visibility="full",
                targets=["gale"],
                tags=["Pixel", "MOBILE-HOTSPOT"],
                legacy_tags="LEGACY-FICTIONAL-FLAG",
                directory=("设备", "FICTIONAL", "无"),
                updated_at="2026-08-03T12:00:00+08:00",
            ),
            "exact": self._create_note(
                title="tun pixel",
                body="older exact title record",
                visibility="full",
                targets=["gale"],
                tags=["REFERENCE"],
                directory=("ARCHIVE", "FICTIONAL", "无"),
                updated_at="2026-08-01T12:00:00+08:00",
            ),
            "codex_only": self._create_note(
                title="TUN CODEX ONLY",
                body="pixel actor boundary",
                visibility="full",
                targets=["codex"],
                tags=["PRIVATE"],
                directory=("PRIVATE", "FICTIONAL", "无"),
            ),
            "hidden": self._create_note(
                title="TUN HIDDEN",
                body="pixel hidden boundary",
                visibility="hidden",
                targets=["gale"],
                tags=["HIDDEN"],
                directory=("HIDDEN", "FICTIONAL", "无"),
            ),
            "deleted": self._create_note(
                title="TUN DELETED",
                body="pixel deleted boundary",
                visibility="full",
                targets=["gale"],
                tags=["DELETED"],
                directory=("TRASH", "FICTIONAL", "无"),
                deleted=True,
            ),
            "redacted": self._create_note(
                title="FICTIONAL_RAW_CANARY",
                body="redacted searchable body",
                visibility="redacted",
                targets=["gale"],
                tags=["REDACTED"],
                directory=("SAFE", "FICTIONAL", "无"),
            ),
            "chinese_phrase": self._create_note(
                title="FICTIONAL INCIDENT NOTES",
                body="凌晨故障复盘与明日修复计划",
                visibility="full",
                targets=["gale"],
                tags=["OPERATIONS"],
                directory=("RUNBOOK", "FICTIONAL", "无"),
            ),
            "technical": self._create_note(
                title="FICTIONAL TECHNICAL TOKENS",
                body=(
                    "v2rayN wg-hi node.js C++ C# 10.77.77.1 "
                    "2026-08-03"
                ),
                visibility="full",
                targets=["gale"],
                tags=["TECHNICAL"],
                directory=("LAB", "FICTIONAL", "无"),
            ),
        }

    def test_gui_database_search_matrix_and_exact_title_priority(self) -> None:
        for query in (
            "tun pixel",
            "pixel tun",
            "TUN PIXEL",
            "Tun Pixel",
            "tun，pixel",
            "tun,pixel",
            "tun：pixel",
            "tun:pixel",
            "tun → pixel",
            "ＴＵＮ　ＰＩＸＥＬ",
            "tun     pixel",
            "tun\npixel",
            "tun\tpixel",
        ):
            with self.subTest(query=query):
                result_ids = [row["id"] for row in search_notes(query, self.database_path)]
                self.assertIn(self.ids["primary"], result_ids)
                self.assertEqual(result_ids[0], self.ids["exact"])

        chinese = [row["id"] for row in search_notes("墨迹 温柔 G", self.database_path)]
        self.assertIn(self.ids["primary"], chinese)
        legacy_tags = [
            row["id"]
            for row in search_notes("legacy fictional", self.database_path)
        ]
        self.assertIn(self.ids["primary"], legacy_tags)
        self.assertNotIn(
            self.ids["primary"],
            [row["id"] for row in search_notes("tun NON_EXISTENT_TERM", self.database_path)],
        )
        self.assertEqual(search_notes("，，，***", self.database_path), [])
        self.assertIsInstance(search_notes('" OR * ( ) :', self.database_path), list)

    def test_gui_ai_view_and_local_mcp_share_and_semantics(self) -> None:
        ai_view = list_ai_visible_notes(
            self.database_path,
            actor_target_id="gale",
            query="pixel tun",
        )
        self.assertIn(self.ids["primary"], {row["id"] for row in ai_view})
        self.assertNotIn(self.ids["codex_only"], {row["id"] for row in ai_view})

        for query in ("tun pixel", "PIXEL TUN", "TUN，Pixel", "墨迹 温柔 G"):
            with self.subTest(query=query):
                result = safe_search_notes(
                    query,
                    "gale",
                    database_path=self.database_path,
                    redaction_rules_path=self.rules_path,
                )
                self.assertIn(self.ids["primary"], {item["note_id"] for item in result["items"]})

        missing = safe_search_notes(
            "tun NON_EXISTENT_TERM",
            "gale",
            database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(missing["total_count"], 0)
        raw = safe_search_notes(
            "FICTIONAL_RAW_CANARY",
            "gale",
            database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        masked = safe_search_notes(
            "FICTIONAL_SAFE_MASK",
            "gale",
            database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(raw["total_count"], 0)
        self.assertEqual(masked["total_count"], 1)

    def test_local_and_remote_gale_search_match_and_remain_read_only(self) -> None:
        mirror_hash_before = _sha256(self.mirror_path)
        source_hash_before = _sha256(self.database_path)
        for query in (
            "tun pixel",
            "PIXEL TUN",
            "TUN，Pixel",
            "墨迹 温柔 G",
            "故障复盘 明日修复计划",
        ):
            with self.subTest(query=query):
                local_ids = {
                    row["source_note_id"]
                    for row in search_gale_mirror(self.mirror_path, query)
                }
                remote_ids = {
                    row["note_id"]
                    for row in search_remote_mirror(self.remote_config, query)
                }
                if query == "故障复盘 明日修复计划":
                    self.assertIn(self.ids["chinese_phrase"], local_ids)
                    self.assertIn(self.ids["chinese_phrase"], remote_ids)
                else:
                    self.assertIn(self.ids["primary"], local_ids)
                    self.assertIn(self.ids["primary"], remote_ids)

        for forbidden_id in (
            self.ids["codex_only"],
            self.ids["hidden"],
            self.ids["deleted"],
        ):
            self.assertNotIn(
                forbidden_id,
                {
                    row["note_id"]
                    for row in search_remote_mirror(self.remote_config, "tun pixel")
                },
            )
        self.assertEqual(search_gale_mirror(self.mirror_path, "，，，***"), [])
        self.assertIsInstance(
            search_remote_mirror(self.remote_config, '" OR * ( ) :'),
            list,
        )
        self.assertEqual(_sha256(self.mirror_path), mirror_hash_before)
        self.assertEqual(_sha256(self.database_path), source_hash_before)
        self.assertEqual(list(self.mirror_root.glob("gale-mirror.db-*")), [])

    def test_technical_tokens_survive_every_search_path(self) -> None:
        for query in (
            "v2rayN",
            "wg-hi",
            "node.js",
            "C++",
            "C#",
            "10.77.77.1",
            "2026-08-03",
        ):
            with self.subTest(query=query):
                self.assertIn(
                    self.ids["technical"],
                    {
                        row["id"]
                        for row in search_notes(query, self.database_path)
                    },
                )
                self.assertIn(
                    self.ids["technical"],
                    {
                        row["note_id"]
                        for row in safe_search_notes(
                            query,
                            "gale",
                            database_path=self.database_path,
                            redaction_rules_path=self.rules_path,
                        )["items"]
                    },
                )
                self.assertIn(
                    self.ids["technical"],
                    {
                        row["source_note_id"]
                        for row in search_gale_mirror(self.mirror_path, query)
                    },
                )
                self.assertIn(
                    self.ids["technical"],
                    {
                        row["note_id"]
                        for row in search_remote_mirror(self.remote_config, query)
                    },
                )

    def test_limit_contract_is_preserved(self) -> None:
        self.assertEqual(len(search_gale_mirror(self.mirror_path, "tun", limit=1)), 1)
        self.assertEqual(len(search_remote_mirror(self.remote_config, "tun", limit=1)), 1)
        with self.assertRaisesRegex(Exception, "invalid_limit"):
            search_remote_mirror(self.remote_config, "tun", limit=21)


if __name__ == "__main__":
    unittest.main()
