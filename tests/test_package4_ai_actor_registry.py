from __future__ import annotations

import unittest

from src.snotes.application.ai_actor_registry import (
    AI_ACTOR_IDS,
    AI_TARGET_REGISTRY,
    resolve_target_id,
    selectable_target_definitions,
    target_definition,
)


class Package4AiActorRegistryTests(unittest.TestCase):
    def test_active_chooser_is_exactly_the_six_product_identities(self) -> None:
        self.assertEqual(
            tuple(
                (item.target_id, item.display_name)
                for item in selectable_target_definitions()
            ),
            (
                ("common", "通用"),
                ("gale", "Gale"),
                ("clawbot", "Clawbot"),
                ("antigravity", "Antigravity"),
                ("claude-code", "Claude Code"),
                ("ke-den", "Ke"),
            ),
        )

    def test_every_identity_has_a_unique_appendable_stable_id(self) -> None:
        stable_ids = tuple(item.target_id for item in AI_TARGET_REGISTRY)
        self.assertEqual(len(stable_ids), len(set(stable_ids)))
        self.assertNotIn("actor1", stable_ids)
        self.assertNotIn("待定1", tuple(item.display_name for item in AI_TARGET_REGISTRY))

    def test_ke_is_a_display_rename_of_the_existing_stable_identity(self) -> None:
        self.assertEqual(resolve_target_id("Ke"), "ke-den")
        self.assertEqual(resolve_target_id("ke-den"), "ke-den")
        self.assertEqual(resolve_target_id("ke-den Claude"), "ke-den")
        self.assertEqual(target_definition("ke-den").display_name, "Ke")

    def test_claude_code_is_distinct_and_legacy_ids_are_never_remapped(self) -> None:
        self.assertIn("claude-code", AI_ACTOR_IDS)
        self.assertNotEqual(resolve_target_id("Claude Code"), "codex")
        self.assertEqual(resolve_target_id("Codex"), "codex")
        self.assertEqual(resolve_target_id("Gemini"), "gemini")
        self.assertFalse(target_definition("codex").selectable)
        self.assertFalse(target_definition("gemini").selectable)


if __name__ == "__main__":
    unittest.main()
