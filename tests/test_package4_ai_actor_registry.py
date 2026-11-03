from __future__ import annotations

import unittest
from unittest.mock import patch

from src.snotes.application import ai_actor_registry
from src.snotes.application.ai_actor_registry import (
    AI_ACTOR_IDS,
    AI_TARGET_REGISTRY,
    AiTargetDefinition,
    resolve_target_id,
    selectable_target_definitions,
    target_definition,
)


class Package4AiActorRegistryTests(unittest.TestCase):
    def test_active_chooser_is_exactly_the_eight_product_identities(self) -> None:
        self.assertEqual(
            tuple(
                (item.target_id, item.display_name)
                for item in selectable_target_definitions()
            ),
            (
                ("common", "通用"),
                ("s", "S"),
                ("gale", "Gale"),
                ("yan", "砚"),
                ("clawbot", "Clawbot"),
                ("antigravity", "Antigravity"),
                ("claude-code", "Claude"),
                ("ke-den", "Ke"),
            ),
        )

    def test_every_identity_has_a_unique_appendable_stable_id(self) -> None:
        stable_ids = tuple(item.target_id for item in AI_TARGET_REGISTRY)
        self.assertEqual(len(stable_ids), len(set(stable_ids)))
        self.assertNotIn("actor1", stable_ids)
        self.assertNotIn("待定1", tuple(item.display_name for item in AI_TARGET_REGISTRY))
        self.assertEqual(stable_ids.count("yan"), 1)
        self.assertEqual(resolve_target_id("砚"), "yan")
        self.assertEqual(target_definition("yan").display_name, "砚")
        self.assertEqual(resolve_target_id("S"), "s")
        self.assertEqual(target_definition("s"), AiTargetDefinition("s", "S", "s"))

    def test_selectable_targets_follow_current_registry_without_static_copy(self) -> None:
        dynamic = AiTargetDefinition(
            "fictional-runtime-actor",
            "Fictional Runtime Actor",
            "fictional_runtime_actor",
        )

        with patch.object(
            ai_actor_registry,
            "AI_TARGET_REGISTRY",
            AI_TARGET_REGISTRY + (dynamic,),
        ):
            self.assertIn(
                ("fictional-runtime-actor", "Fictional Runtime Actor"),
                tuple(
                    (item.target_id, item.display_name)
                    for item in selectable_target_definitions()
                ),
            )

    def test_ke_is_a_display_rename_of_the_existing_stable_identity(self) -> None:
        self.assertEqual(resolve_target_id("Ke"), "ke-den")
        self.assertEqual(resolve_target_id("ke-den"), "ke-den")
        self.assertEqual(resolve_target_id("ke-den Claude"), "ke-den")
        self.assertEqual(target_definition("ke-den").display_name, "Ke")

    def test_claude_code_is_distinct_and_legacy_ids_are_never_remapped(self) -> None:
        self.assertIn("claude-code", AI_ACTOR_IDS)
        self.assertEqual(resolve_target_id("Claude"), "claude-code")
        self.assertEqual(resolve_target_id("Codex"), "codex")
        self.assertEqual(resolve_target_id("Gemini"), "gemini")
        self.assertFalse(target_definition("codex").selectable)
        self.assertFalse(target_definition("gemini").selectable)


if __name__ == "__main__":
    unittest.main()
