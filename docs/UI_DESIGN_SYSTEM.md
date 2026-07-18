# S-notes UI Design System

This document defines the UI R2 visual contract. It applies to the main window,
all dialogs and utility windows, menus, popups, empty states, and future UI work.

## Themes

S-notes provides exactly three user-selectable themes:

- `dark` / 夜间: deep charcoal surfaces with a restrained green accent for
  long reading sessions. It does not use a full pure-black canvas.
- `light-slate` / 蓝灰: a cool, serious light theme with a white editor,
  blue-gray sidebars, and a restrained gray-blue accent.
- `light-clay` / 陶土: a white editor with warm-gray sidebars and a limited
  clay-red accent. It must not drift into cream, cute, or orange-heavy styling.

There is no follow-system mode in UI R2. The selected theme is stored in
`~/Library/Application Support/S-notes/config/ui_settings.ini`, so source and
packaged App runs share the last explicit choice.

## Token Architecture

`src/theme.py` is the single raw-color source:

- `ThemeTokens` defines the complete color contract.
- `THEMES` contains the three token sets.
- `build_app_style()` generates one shared QSS structure from a selected token
  set; the project does not keep three copied QSS files.
- `apply_theme()` updates the application stylesheet without reloading notes.
- `themed_icon()` renders repository SVG paths with the current token color.
- Rich-text document defaults contain typography only.
- `apply_editor_document_theme()` and `QTextEdit.ExtraSelection` apply runtime
  document colors without rewriting note HTML. Legacy fixed foreground and code
  backgrounds are normalized when content loads, and new input remains free of
  stored theme colors.

Components must use semantic token roles such as `sidebar`, `surface`, `text`,
`accent`, `warning`, and `focus`. New raw hex, RGB, or RGBA values are not
allowed outside the token definitions.

## Layout Relationships

- The left sidebar uses the `sidebar` layer, with `sidebar_hover` and
  `sidebar_selected` for interaction states.
- The editor uses the `editor` layer. Toolbars and metadata controls use
  `surface` levels and spacing instead of borders or shadows.
- The related-notes drawer uses the same sidebar layer and interaction language
  as the left sidebar. It must not become a white editor-like island.
- The left sidebar starts at a 280px baseline and remains user-resizable.
- The centered editor sheet has a final maximum width of 800px. Horizontal
  margins prefer 48px and reduce to 24px or 16px when space is limited.
- Note-list rows have a maximum height of 32px, with title left aligned and date
  right aligned.

## Ten Hard Rules

1. Colors come only from the centralized theme token source. Components,
   windows, QSS fragments, and business code must not introduce raw colors.
2. Layout spacing uses multiples of four. The usual values are 4, 8, 12, 16,
   24, 32, 40, and 48. One or two pixels are reserved for icon strokes, focus
   outlines, and the selected-row accent marker.
3. UI font sizes are limited to 11, 12.5, 13, 15, and 26px. Bold is reserved for
   page or note titles, active items, and meaningful compact headings.
4. Ordinary controls use a 6px radius; cards, menus, and popups use 8px or 10px;
   window-level containers may use 12px. Decorative circles and pill buttons
   are not part of the system.
5. Icons use Lucide-style linear SVG paths at 14px with `stroke-width="2"`.
   Emoji, Unicode arrows, and character stand-ins are not icons.
6. Every interactive control provides hover, pressed or checked, and keyboard
   focus feedback. Any future transition must finish within 150ms.
7. Hierarchy comes from background lightness, spacing, and text levels. Borders
   and shadows are not used for visual layering; focus outlines, the narrow
   selection marker, native macOS window edges, and semantic status marks are
   the only exceptions.
8. List rows never exceed 32px. Dense, repeat-use workflows take priority over
   decorative whitespace and oversized cards.
9. Elements in a container share horizontal alignment axes. This applies to the
   sidebar, editor title and body, related drawer, and every dialog form.
10. Every new visible element must carry a function or information. Decorative
    labels, wrappers, dividers, and icons without a concrete purpose are removed.

## Icon Rules

SVG files live in `src/icons/`. Their repository source uses `currentColor`,
14px dimensions, a 24px view box, and a 2px stroke. `themed_icon()` supplies the
semantic token color at render time. Icon buttons require a concise tooltip;
plain-text commands may remain plain text when an icon adds no clarity.

## Markdown Marker Cleanup

The editor toolbar provides a compact `remove-formatting` icon command named
"清除 Markdown 标记". It processes only the selection when one exists and the
full note otherwise. The conversion conservatively removes source markers while
preserving text, fenced-code content, URLs, hashtags, ordinary identifiers,
four-space-indented code, Markdown tables, triple single quotes, and unmatched
fences. The replacement is one undoable edit block, uses the existing autosave
path, and does not store theme colors in note HTML.

## New UI Checklist

- Does every color resolve through `ThemeTokens`?
- Are all layout margins and gaps multiples of four?
- Are font sizes and radii in the allowed sets?
- Are icons Lucide-style, 14px, and token colored?
- Are hover, pressed or checked, focus, disabled, warning, and empty states
  covered where applicable?
- Is hierarchy expressed without layering borders or shadows?
- Are list rows 32px or shorter and alignment axes shared?
- Does the UI work in all three themes, including menus and dialogs?
- Does a theme switch preserve unsaved text, cursor, selection, and scrolling?
- Does the change preserve database, autosave, AI visibility, export, deletion,
  and related-note semantics?
