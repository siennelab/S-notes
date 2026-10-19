"""Fictional fixtures for the Step 5 actor-specific Cloud read path."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.cloud_read_mcp import binding_for_effective_uid
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.domain.actor_mirror import ActorMirrorRecord
from src.snotes.storage.actor_mirror import write_actor_mirror
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.mcp.cloud_read_server import (
    call_cloud_read_tool,
    cloud_read_tools,
)


class Step05CloudReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="snotes-step05-cloud-read-"
        )
        self.root = Path(self.temporary_directory.name)
        self.claude_path = self.root / "claude-current.db"
        self.clawbot_path = self.root / "clawbot-current.db"
        self.common_id = 101
        self.claude_id = 102
        self.clawbot_id = 103
        self.redacted_id = 104
        self.full_id = 105
        self.hidden_id = 106
        self.chinese_title_id = 107
        self.chinese_body_id = 108
        self.english_tokens_id = 109
        self.newer_nonmatching_id = 110
        self.c_only_decoy_id = 111
        common = self._record(
            self.common_id,
            title="Fictional common",
            safe_text="common aurora",
            safe_markdown="# Common\n\ncommon aurora",
            targets=("common",),
        )
        redacted = self._record(
            self.redacted_id,
            title="Fictional redacted",
            safe_text="masked nebula [REDACTED]",
            safe_markdown=None,
            targets=("common",),
            visibility="redacted",
            redaction_count=1,
        )
        full = self._record(
            self.full_id,
            title="Fictional full Markdown",
            safe_text="canonical bold orbit",
            safe_markdown="# Canonical\n\n**bold orbit**",
            targets=("common",),
        )
        write_actor_mirror(
            (
                common,
                self._record(
                    self.claude_id,
                    title="Claude fictional",
                    safe_text="claude comet",
                    safe_markdown="# Claude\n\nclaude comet",
                    targets=("claude-code",),
                ),
                redacted,
                full,
                self._record(
                    self.chinese_title_id,
                    title="S的熊点子们",
                    safe_text="记录一些温柔又古怪的灵感",
                    safe_markdown="# S的熊点子们\n\n记录一些温柔又古怪的灵感",
                    targets=("common",),
                    source_updated_at="2026-09-01T12:00:00+08:00",
                ),
                self._record(
                    self.chinese_body_id,
                    title="值班记录",
                    safe_text="凌晨故障复盘与明日修复计划",
                    safe_markdown="# 值班记录\n\n凌晨故障复盘与明日修复计划",
                    targets=("common",),
                    source_updated_at="2026-09-02T12:00:00+08:00",
                ),
                self._record(
                    self.english_tokens_id,
                    title="Async worker notes",
                    safe_text="Node.js and C++ retry bridge for C# clients",
                    safe_markdown="# Async worker notes\n\nNode.js and C++ retry bridge for C# clients",
                    targets=("common",),
                    source_updated_at="2026-09-03T12:00:00+08:00",
                ),
                self._record(
                    self.newer_nonmatching_id,
                    title="Newest unrelated note",
                    safe_text="Nothing from the requested incident lives here",
                    safe_markdown="# Newest unrelated note\n\nNothing relevant",
                    targets=("common",),
                    source_updated_at="2026-10-01T12:00:00+08:00",
                ),
                self._record(
                    self.c_only_decoy_id,
                    title="C bridge notes",
                    safe_text="Node.js retry bridge for C clients",
                    safe_markdown="# C bridge notes\n\nNode.js retry bridge for C clients",
                    targets=("common",),
                    source_updated_at="2026-09-04T12:00:00+08:00",
                ),
            ),
            self.claude_path,
            actor="claude-code",
            source_schema="snotes-markdown-v1",
        )
        write_actor_mirror(
            (
                common,
                self._record(
                    self.clawbot_id,
                    title="Clawbot fictional",
                    safe_text="clawbot comet",
                    safe_markdown="# Clawbot\n\nclawbot comet",
                    targets=("clawbot",),
                ),
                redacted,
                full,
            ),
            self.clawbot_path,
            actor="clawbot",
            source_schema="snotes-markdown-v1",
        )
        self.claude = CloudReadService(SQLiteCloudMirrorReader(self.claude_path))
        self.clawbot = CloudReadService(SQLiteCloudMirrorReader(self.clawbot_path))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_actor_mirrors_return_common_and_only_their_specific_note(self) -> None:
        self.assertEqual(self.claude.get_note(self.common_id)["status"], "ok")
        self.assertEqual(self.clawbot.get_note(self.common_id)["status"], "ok")
        self.assertEqual(self.claude.get_note(self.claude_id)["safe_text"], "claude comet")
        self.assertEqual(self.clawbot.get_note(self.claude_id)["status"], "not_found")
        self.assertEqual(self.clawbot.get_note(self.clawbot_id)["safe_text"], "clawbot comet")
        self.assertEqual(self.claude.get_note(self.clawbot_id)["status"], "not_found")
        self.assertEqual(self.claude.get_note(self.hidden_id)["status"], "not_found")

    def test_search_reads_only_safe_fields_from_current_actor_mirror(self) -> None:
        claude = self.claude.search_notes("comet")
        clawbot = self.clawbot.search_notes("comet")
        self.assertEqual([row["note_id"] for row in claude["results"]], [self.claude_id])
        self.assertEqual([row["note_id"] for row in clawbot["results"]], [self.clawbot_id])
        self.assertEqual(claude["results"][0]["safe_text"], "claude comet")
        self.assertNotIn("safe_markdown", claude["results"][0])

    def test_search_uses_normalized_substrings_and_multi_keyword_and_semantics(self) -> None:
        cases = (
            ("熊点子", [self.chinese_title_id]),
            ("S的熊点子们", [self.chinese_title_id]),
            ("故障复盘", [self.chinese_body_id]),
            ("绝对不存在的测试词", []),
            ("ＡＳＹＮＣ retry", [self.english_tokens_id]),
            ("node.js C++ C#", [self.english_tokens_id]),
        )
        for query, expected_ids in cases:
            with self.subTest(query=query):
                response = self.claude.search_notes(query)
                self.assertEqual(
                    [row["note_id"] for row in response["results"]],
                    expected_ids,
                )

    def test_search_applies_limit_after_substring_matching(self) -> None:
        response = self.claude.search_notes("故障复盘", limit=1)
        self.assertEqual(
            [row["note_id"] for row in response["results"]],
            [self.chinese_body_id],
        )
        ranked_response = self.claude.search_notes("Fictional", limit=1)
        self.assertEqual(ranked_response["result_count"], 1)
        self.assertEqual(ranked_response["results"][0]["note_id"], self.full_id)

    def test_plain_text_and_markdown_semantics_are_distinct(self) -> None:
        redacted = self.claude.get_note(self.redacted_id)
        self.assertEqual(redacted["status"], "ok")
        self.assertEqual(redacted["safe_text"], "masked nebula [REDACTED]")
        self.assertEqual(
            self.claude.get_note_markdown(self.redacted_id),
            {"status": "not_found", "note_id": self.redacted_id},
        )
        markdown = self.claude.get_note_markdown(self.full_id)
        self.assertEqual(markdown["markdown"], "# Canonical\n\n**bold orbit**")
        self.assertEqual(markdown["version"], f"v-{self.full_id}")
        self.assertEqual(markdown["dialect"], "snotes-markdown-v1")
        self.assertEqual(
            self.claude.get_note_markdown(self.hidden_id),
            {"status": "not_found", "note_id": self.hidden_id},
        )

    def test_next_request_opens_atomic_replacement_inode(self) -> None:
        self.assertEqual(self.claude.get_note(self.common_id)["safe_text"], "common aurora")
        replacement = self.root / "next.db"
        write_actor_mirror(
            (
                self._record(
                    self.common_id,
                    title="Fictional common",
                    safe_text="common aurora changed",
                    safe_markdown="# Common\n\ncommon aurora changed",
                    targets=("common",),
                    version="v-next",
                ),
            ),
            replacement,
            actor="claude-code",
            source_schema="snotes-markdown-v1",
        )
        before_inode = self.claude_path.stat().st_ino
        os.replace(replacement, self.claude_path)
        self.assertNotEqual(self.claude_path.stat().st_ino, before_inode)
        updated = self.claude.get_note(self.common_id)
        self.assertEqual(updated["safe_text"], "common aurora changed")
        self.assertEqual(updated["version"], "v-next")

    def test_protocol_surface_has_six_read_tools_and_no_actor_input(self) -> None:
        tools = cloud_read_tools()
        self.assertEqual(
            [tool.name for tool in tools],
            [
                "search_notes",
                "get_note",
                "get_note_markdown",
                "list_directories",
                "list_tags",
                "list_ai_metadata_options",
            ],
        )
        for tool in tools:
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertNotIn("actor", tool.inputSchema["properties"])
        response = call_cloud_read_tool(
            self.claude,
            "get_note_markdown",
            {"note_id": self.redacted_id, "actor": "clawbot"},
        )
        self.assertEqual(response["status"], "not_found")

    def test_production_identity_mapping_is_fixed_to_uid_and_actor_path(self) -> None:
        claude = binding_for_effective_uid(2001)
        openclaw = binding_for_effective_uid(2002)
        self.assertEqual(claude.actor, "claude-code")
        self.assertEqual(
            claude.mirror_path,
            Path("/srv/snotes/mirror/claude-code/current.db"),
        )
        self.assertEqual(openclaw.actor, "clawbot")
        self.assertEqual(
            openclaw.mirror_path,
            Path("/srv/snotes/mirror/clawbot/current.db"),
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported_os_identity"):
            binding_for_effective_uid(2003)

    def _record(
        self,
        note_id: int,
        *,
        title: str,
        safe_text: str,
        safe_markdown: str | None,
        targets: tuple[str, ...],
        visibility: str = "full",
        redaction_count: int = 0,
        version: str | None = None,
        source_updated_at: str = "2026-09-29T12:00:00+08:00",
    ) -> ActorMirrorRecord:
        return ActorMirrorRecord(
            source_note_id=note_id,
            title=title,
            safe_text=safe_text,
            safe_markdown=safe_markdown,
            directory="Fictional / Step5",
            tags=("fictional",),
            source_updated_at=source_updated_at,
            source_version=version or f"v-{note_id}",
            visibility=visibility,
            redaction_count=redaction_count,
            ai_visibility=visibility,
            ai_targets=targets,
        )


if __name__ == "__main__":
    unittest.main()
