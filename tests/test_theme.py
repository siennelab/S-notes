from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication, QStyle

from src.editor import NotesEditor
from src.floating_combo import FloatingComboBox
from src.main_window import (
    EDITOR_BOTTOM_MARGIN,
    EDITOR_MEDIUM_SIDE_MARGIN,
    EDITOR_NARROW_SIDE_MARGIN,
    EDITOR_TARGET_MAX_WIDTH,
    EDITOR_WIDE_SIDE_MARGIN,
    TITLE_SEPARATOR_HEIGHT,
    editor_horizontal_metrics,
)
from src.theme import (
    ICON_DIR,
    THEMES,
    THEME_SWATCH_COLORS,
    THEME_SWATCH_RING_COLORS,
    THEME_SETTINGS_PATH,
    apply_theme,
    build_app_style,
    current_theme,
    load_saved_theme,
    save_theme,
    set_current_theme,
)


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = QColor(color).getRgbF()[:3]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def rendered_foreground(editor: NotesEditor, position: int) -> str | None:
    for selection in reversed(editor.extraSelections()):
        if selection.cursor.selectionStart() <= position < selection.cursor.selectionEnd():
            brush = selection.format.foreground()
            if brush.style() != Qt.NoBrush:
                return brush.color().name().upper()
    return None


def qt_text_position(text: str, substring: str) -> int:
    prefix = text[: text.index(substring)]
    return len(prefix.encode("utf-16-le")) // 2


class ThemeSystemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_three_registered_themes_share_the_same_token_shape(self) -> None:
        self.assertEqual(set(THEMES), {"dark", "light-slate", "light-clay"})
        token_fields = set(THEMES["dark"].__dataclass_fields__)
        for tokens in THEMES.values():
            self.assertEqual(set(tokens.__dataclass_fields__), token_fields)

    def test_document_theme_roles_are_token_backed_and_readable(self) -> None:
        for tokens in THEMES.values():
            self.assertEqual(tokens.document_text, tokens.text)
            self.assertEqual(tokens.document_secondary, tokens.text_muted)
            self.assertEqual(tokens.document_link, tokens.accent)
            self.assertEqual(tokens.document_code, tokens.text)
            self.assertGreaterEqual(
                contrast_ratio(tokens.document_text, tokens.editor_paper),
                4.5,
                tokens.name,
            )

    def test_light_slate_sidebar_roles_have_clear_visual_levels(self) -> None:
        tokens = THEMES["light-slate"]
        self.assertGreater(
            QColor(tokens.sidebar_control).lightnessF(),
            QColor(tokens.sidebar).lightnessF(),
        )
        self.assertGreater(
            QColor(tokens.sidebar_selected).lightnessF(),
            QColor(tokens.sidebar_hover).lightnessF(),
        )
        self.assertGreaterEqual(
            contrast_ratio(tokens.sidebar_text, tokens.sidebar),
            4.5,
        )
        self.assertEqual(tokens.window, "#F0F2F5")
        self.assertEqual(tokens.editor, "#F0F2F5")
        self.assertEqual(tokens.editor_paper, "#F0F2F5")
        self.assertEqual(tokens.editor_border, "#DCDCDC")

        style = build_app_style(tokens.name)
        self.assertIn("#themeSwatch[themeName=\"light-slate\"]", style)
        self.assertIn(
            f"#mainSplitter::handle {{ background: {tokens.sidebar_separator}; }}",
            style,
        )
        self.assertIn(
            f"#noteTable::item:hover {{ background: {tokens.sidebar_hover};",
            style,
        )
        self.assertIn(
            f"#noteTable::item:selected {{ background: {tokens.sidebar_selected};",
            style,
        )
        self.assertIn(
            f"#sidebarComboPopupView::item:hover,\n"
            f"#sidebarComboPopupView::item:selected {{ background: {tokens.sidebar_selected}; "
            f"color: {tokens.sidebar_text}; }}",
            style,
        )
        self.assertIn(
            f"#sidebarComboPopupView::item:disabled {{ color: "
            f"{tokens.sidebar_text_subtle}; }}",
            style,
        )

    def test_sidebar_combo_popup_uses_readable_theme_specific_item_colors(self) -> None:
        previous_theme = current_theme().name
        combo = FloatingComboBox(popup_theme="dark")
        delegate = combo.view().itemDelegate()
        try:
            for theme_name in ("dark", "light-slate", "light-clay"):
                tokens = set_current_theme(theme_name)
                normal_background, normal_text = delegate.item_colors(
                    QStyle.State_Enabled
                )
                hover_background, hover_text = delegate.item_colors(
                    QStyle.State_Enabled | QStyle.State_MouseOver
                )
                selected_background, selected_text = delegate.item_colors(
                    QStyle.State_Enabled | QStyle.State_Selected
                )
                disabled_background, disabled_text = delegate.item_colors(
                    QStyle.State_None
                )
                self.assertEqual(normal_background.name().upper(), tokens.sidebar)
                self.assertEqual(normal_text.name().upper(), tokens.sidebar_text)
                self.assertEqual(
                    hover_background.name().upper(), tokens.sidebar_selected
                )
                self.assertEqual(hover_text.name().upper(), tokens.sidebar_text)
                self.assertEqual(
                    selected_background.name().upper(), tokens.sidebar_selected
                )
                self.assertEqual(selected_text.name().upper(), tokens.sidebar_text)
                self.assertEqual(disabled_background.name().upper(), tokens.sidebar)
                self.assertEqual(
                    disabled_text.name().upper(), tokens.sidebar_text_subtle
                )
                self.assertGreaterEqual(
                    contrast_ratio(tokens.sidebar_text, tokens.sidebar_selected),
                    4.5,
                    theme_name,
                )

            set_current_theme("light-slate")
            self.assertEqual(combo.check_icon_role, "sidebar_text_muted")
            for theme_name in ("dark", "light-clay"):
                set_current_theme(theme_name)
                self.assertEqual(combo.check_icon_role, "accent")
        finally:
            set_current_theme(previous_theme)

    def test_all_editor_surfaces_use_approved_single_original_color(self) -> None:
        expected_editor_colors = {
            "dark": "#1B1D1E",
            "light-slate": "#F0F2F5",
            "light-clay": "#FFFFFF",
        }
        for theme_name, expected_color in expected_editor_colors.items():
            tokens = THEMES[theme_name]
            self.assertEqual(tokens.editor, expected_color)
            self.assertEqual(tokens.editor_paper, expected_color)

    def test_theme_swatch_colors_are_fixed_representatives(self) -> None:
        self.assertEqual(
            THEME_SWATCH_COLORS,
            {
                "dark": "#1B1D1E",
                "light-slate": "#5F768B",
                "light-clay": "#A84B2F",
            },
        )
        style = build_app_style("light-slate")
        for theme_name, color in THEME_SWATCH_COLORS.items():
            self.assertIn(
                f'#themeSwatch[themeName="{theme_name}"] {{ background: {color}; }}',
                style,
            )
        self.assertEqual(
            THEME_SWATCH_RING_COLORS,
            {
                "dark": "#555B60",
                "light-slate": "#8FA1B1",
                "light-clay": "#C6816A",
            },
        )
        self.assertTrue(
            all(
                color.upper() not in {"#FFFFFF", "#FFF"}
                for color in THEME_SWATCH_RING_COLORS.values()
            )
        )
        self.assertIn(
            '#themeSwatch { border: 1px solid transparent; border-radius: 7px;',
            style,
        )
        self.assertIn(
            '#themeSwatch[active="true"] { border-width: 1px; border-radius: 8px;',
            style,
        )
        for theme_name, color in THEME_SWATCH_RING_COLORS.items():
            self.assertIn(
                f'#themeSwatch[active="true"][themeName="{theme_name}"] '
                f'{{ border-color: {color}; }}',
                style,
            )

    def test_editor_widens_with_a_stable_left_axis(self) -> None:
        editor_root_width = 1600
        left_margin, right_margin, leading_space = editor_horizontal_metrics(
            editor_root_width
        )
        target_width = min(
            EDITOR_TARGET_MAX_WIDTH,
            editor_root_width - left_margin - right_margin - leading_space,
        )

        self.assertEqual(
            (left_margin, right_margin, leading_space),
            (EDITOR_WIDE_SIDE_MARGIN, EDITOR_WIDE_SIDE_MARGIN, 0),
        )
        self.assertEqual(target_width, 1280)
        self.assertGreater(target_width, 1000)

    def test_editor_narrow_margins_do_not_overflow(self) -> None:
        widths_and_margins = (
            (896, EDITOR_WIDE_SIDE_MARGIN),
            (895, EDITOR_MEDIUM_SIDE_MARGIN),
            (640, EDITOR_MEDIUM_SIDE_MARGIN),
            (639, EDITOR_NARROW_SIDE_MARGIN),
        )
        for editor_root_width, expected_margin in widths_and_margins:
            left_margin, right_margin, leading_space = editor_horizontal_metrics(
                editor_root_width
            )
            available_width = editor_root_width - left_margin - right_margin
            target_width = min(
                EDITOR_TARGET_MAX_WIDTH,
                available_width - leading_space,
            )
            self.assertEqual(left_margin, expected_margin)
            self.assertEqual(right_margin, expected_margin)
            self.assertGreaterEqual(target_width, 0)
            self.assertLessEqual(leading_space + target_width, available_width)

    def test_theme_preference_round_trips_through_application_support_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "config" / "ui_settings.ini"
            with patch("src.theme.THEME_SETTINGS_PATH", settings_path):
                self.assertEqual(save_theme("light-clay"), "light-clay")
                self.assertEqual(load_saved_theme(), "light-clay")
                self.assertTrue(settings_path.is_file())
        self.assertNotEqual(THEME_SETTINGS_PATH, settings_path)

    def test_theme_switch_preserves_editor_content_selection_and_html(self) -> None:
        apply_theme(self.app, "light-slate")
        editor = NotesEditor()
        editor.show()
        self.app.processEvents()
        editor.setPlainText("alpha beta gamma")
        cursor = editor.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(10, QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)
        before_text = editor.toPlainText()
        before_html = editor.toHtml()
        before_selection = editor.textCursor().selectedText()
        editor.document().setModified(False)
        text_change_events = []
        editor.textChanged.connect(lambda: text_change_events.append(True))

        for theme_name in ("dark", "light-clay", "light-slate"):
            apply_theme(self.app, theme_name)
            editor.apply_theme()
            self.app.processEvents()
            self.assertEqual(editor.toPlainText(), before_text)
            self.assertEqual(editor.toHtml(), before_html)
            self.assertEqual(editor.textCursor().selectedText(), before_selection)
            self.assertFalse(editor.document().isModified())
            self.assertEqual(text_change_events, [])
        editor.hide()

    def test_legacy_foreground_is_overridden_without_rewriting_rich_text(self) -> None:
        apply_theme(self.app, "light-slate")
        editor = NotesEditor()
        legacy_html = """
        <h2><span style="color:#333333">旧标题</span></h2>
        <p><span style="color:#333333; font-weight:600; font-style:italic">
        旧正文 mixed 中文 😀</span>
        <a href="https://example.com" style="color:#333333">链接</a></p>
        """
        editor.set_note_content(legacy_html)
        self.assertFalse(editor.document().isModified())
        before_text = editor.toPlainText()
        before_html = editor.toHtml()
        self.assertNotIn("#333333", before_html.lower())
        body_position = qt_text_position(before_text, "旧正文")
        link_position = qt_text_position(before_text, "链接")

        for theme_name, tokens in THEMES.items():
            apply_theme(self.app, theme_name)
            editor.apply_theme()
            self.app.processEvents()
            self.assertEqual(
                rendered_foreground(editor, body_position),
                tokens.document_text.upper(),
            )
            self.assertEqual(
                rendered_foreground(editor, link_position),
                tokens.document_link.upper(),
            )
            self.assertEqual(editor.toPlainText(), before_text)
            self.assertEqual(editor.toHtml(), before_html)
            self.assertFalse(editor.document().isModified())

        body_cursor = QTextCursor(editor.document())
        body_cursor.setPosition(body_position)
        body_format = body_cursor.charFormat()
        self.assertGreater(body_format.fontWeight(), 400)
        self.assertTrue(body_format.fontItalic())

    def test_new_input_uses_theme_display_color_without_storing_it(self) -> None:
        apply_theme(self.app, "dark")
        editor = NotesEditor()
        editor.set_note_content(
            '<p><span style="color:#333333">旧正文</span></p>'
        )
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        editor.setTextCursor(cursor)
        editor.insertPlainText(" 新输入")
        self.app.processEvents()

        inserted_position = editor.toPlainText().index("新输入")
        inserted_cursor = QTextCursor(editor.document())
        inserted_cursor.setPosition(inserted_position)
        self.assertFalse(
            inserted_cursor.charFormat().hasProperty(QTextFormat.ForegroundBrush)
        )
        self.assertEqual(
            rendered_foreground(editor, inserted_position),
            THEMES["dark"].document_text.upper(),
        )
        self.assertNotIn(THEMES["dark"].document_text.lower(), editor.toHtml().lower())

    def test_legacy_code_background_is_replaced_by_theme_overlay(self) -> None:
        apply_theme(self.app, "dark")
        editor = NotesEditor()
        editor.set_note_content(
            """
            <pre style="margin-left:16px; margin-right:8px; margin-top:8px;
                        margin-bottom:8px; background-color:#f0f0f0">
            <span style="font-family:'SF Mono'; color:#333333">def hello():</span>
            </pre>
            """
        )
        code_position = qt_text_position(editor.toPlainText(), "def hello")
        code_block = editor.document().findBlock(code_position)
        self.assertFalse(
            code_block.blockFormat().hasProperty(QTextFormat.BackgroundBrush)
        )
        self.assertNotIn("#f0f0f0", editor.toHtml().lower())

        backgrounds = [
            selection.format.background().color().name().upper()
            for selection in editor.extraSelections()
            if selection.cursor.selectionStart()
            <= code_position
            < selection.cursor.selectionEnd()
            and selection.format.hasProperty(QTextFormat.BackgroundBrush)
        ]
        self.assertIn(THEMES["dark"].surface.upper(), backgrounds)

    def test_shared_qss_avoids_shadow_and_rgba_layering(self) -> None:
        for theme_name, tokens in THEMES.items():
            style = build_app_style(theme_name)
            self.assertNotIn("box-shadow", style)
            self.assertNotIn("rgba(", style)
            self.assertIn("#linkDrawer", style)
            self.assertIn("#sidebar", style)
            self.assertIn(f"background: {tokens.editor_paper};", style)
            self.assertIn(f"border: 1px solid {tokens.editor_border};", style)
            self.assertIn(f"background: {tokens.title_separator};", style)
            self.assertIn(f"min-height: {TITLE_SEPARATOR_HEIGHT}px;", style)
            self.assertIn(f"max-height: {TITLE_SEPARATOR_HEIGHT}px;", style)

        self.assertEqual(EDITOR_BOTTOM_MARGIN, 12)

    def test_dark_theme_no_longer_contains_the_old_green_accent(self) -> None:
        dark = THEMES["dark"]
        style = build_app_style("dark").upper()
        self.assertEqual(dark.accent, "#B97855")
        self.assertEqual(dark.focus, "#CB8A67")
        for old_green in ("#48B981", "#65C995", "#253C32"):
            self.assertNotIn(old_green, style)

    def test_repository_icons_are_lucide_sized_and_token_colored(self) -> None:
        for icon_path in ICON_DIR.glob("*.svg"):
            source = icon_path.read_text(encoding="utf-8")
            self.assertIn('width="14"', source, icon_path.name)
            self.assertIn('height="14"', source, icon_path.name)
            self.assertIn('stroke-width="2"', source, icon_path.name)
            self.assertIn("currentColor", source, icon_path.name)
            self.assertIsNone(re.search(r"#[0-9A-Fa-f]{3,8}", source), icon_path.name)


if __name__ == "__main__":
    unittest.main()
