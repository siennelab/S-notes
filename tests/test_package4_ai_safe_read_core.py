from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.snotes.application.ai_safe_read_service import (
    AI_ACTOR_IDS,
    AI_TARGET_IDS,
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_HIDDEN,
    AI_VISIBILITY_REDACTED,
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError,
    apply_redactions,
)


class Package4AiSafeReadCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.rules_path = Path(self.temporary.name) / "fictional-redaction.json"
        self.rules_path.write_text(
            json.dumps(
                {
                    "replacements": [
                        {"from": "FictionalSecret", "to": "[MASKED]"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.service = AiSafeReadService(self.rules_path)
        self.source = AiSafeReadSource(
            note_id=41,
            title="Fictional title",
            content="Fictional body",
            visibility=AI_VISIBILITY_FULL,
            targets=("common",),
            directory="Fictional / 无 / 无",
            tags=("fictional-tag",),
        )

    def test_common_full_is_readable_by_every_registered_actor(self) -> None:
        self.assertIn("s", AI_TARGET_IDS)
        self.assertIn("claude-code", AI_TARGET_IDS)
        for actor in AI_ACTOR_IDS:
            with self.subTest(actor=actor):
                result = self.service.read_note(actor, self.source)
                self.assertIsNotNone(result)
                self.assertEqual(result.content, "Fictional body")
                self.assertFalse(result.redacted)

    def test_actor_only_full_is_readable_by_that_actor(self) -> None:
        source = replace(self.source, targets=("claude-code",))

        result = self.service.read_note("claude-code", source)

        self.assertIsNotNone(result)
        self.assertEqual(result.content, source.content)

    def test_legacy_codex_grant_is_not_reinterpreted_as_claude_code(self) -> None:
        source = replace(self.source, targets=("codex",))

        self.assertIsNotNone(self.service.read_note("codex", source))
        self.assertIsNone(self.service.read_note("claude-code", source))

    def test_unauthorized_or_unknown_actor_cannot_read(self) -> None:
        source = replace(self.source, targets=("claude-code",))

        self.assertIsNone(self.service.read_note("gale", source))
        self.assertIsNone(self.service.read_note("unknown-actor", source))
        self.assertIsNone(self.service.read_note("common", source))

    def test_hidden_note_cannot_be_read(self) -> None:
        source = replace(
            self.source,
            visibility=AI_VISIBILITY_HIDDEN,
            targets=("gale",),
        )

        self.assertIsNone(self.service.read_note("gale", source))

    def test_redacted_read_materializes_all_safe_fields_without_mutation(self) -> None:
        source = AiSafeReadSource(
            note_id=42,
            title="fictionalsecret title",
            content="FICTIONALSECRET body",
            visibility=AI_VISIBILITY_REDACTED,
            targets=("gale",),
            directory="FictionalSecret / 无 / 无",
            tags=("FiCtIoNaLsEcReT-tag",),
        )

        result = self.service.read_note("gale", source)

        self.assertIsNotNone(result)
        self.assertEqual(result.title, "[MASKED] title")
        self.assertEqual(result.content, "[MASKED] body")
        self.assertEqual(result.directory, "[MASKED] / 无 / 无")
        self.assertEqual(result.tags, ("[MASKED]-tag",))
        self.assertTrue(result.redacted)
        self.assertEqual(result.redaction_count, 4)
        self.assertEqual(source.content, "FICTIONALSECRET body")
        self.assertEqual(source.tags, ("FiCtIoNaLsEcReT-tag",))

    def test_redacted_read_fails_closed_for_unavailable_rules(self) -> None:
        source = replace(
            self.source,
            content="FICTIONAL_RAW_CANARY",
            visibility=AI_VISIBILITY_REDACTED,
            targets=("gale",),
        )
        cases = {
            "missing": (None, "规则文件不存在"),
            "invalid-json": ("{ invalid", "规则文件无法解析"),
            "empty-replacements": (
                json.dumps({"replacements": []}),
                "没有有效 replacement",
            ),
        }
        for name, (rules, message) in cases.items():
            with self.subTest(name=name):
                if rules is None:
                    self.rules_path.unlink(missing_ok=True)
                else:
                    self.rules_path.write_text(rules, encoding="utf-8")
                with self.assertRaisesRegex(RedactionUnavailableError, message):
                    self.service.read_note("gale", source)

    def test_missing_redaction_rules_do_not_block_full_read(self) -> None:
        self.rules_path.unlink()

        result = self.service.read_note("gale", self.source)

        self.assertIsNotNone(result)
        self.assertEqual(result.content, "Fictional body")

    def test_public_redaction_is_case_insensitive(self) -> None:
        result, count = apply_redactions(
            "fictionalsecret FICTIONALSECRET FictionalSecret",
            (("FictionalSecret", "[MASKED]"),),
        )

        self.assertEqual(result, "[MASKED] [MASKED] [MASKED]")
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
