from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.app_paths import AI_READBOX_DIR
from src.ai_targets import (
    AI_TARGET_COMMON,
    AI_VISIBILITY_REDACTED,
    target_slug,
)
from src.database import (
    DATABASE_PATH,
    get_note_ai_targets,
    list_ai_safe_notes_for_target,
)
from src.redaction import REDACTION_RULES_PATH, apply_redactions, load_redaction_rules
from src.sensitive_scan import (
    SensitiveFinding,
    findings_to_manifest,
    has_sensitive_findings,
    merge_sensitive_findings,
    scan_sensitive_text,
)
from src.tag_manager import parse_tags
from src.time_utils import format_display_datetime, now_shanghai


@dataclass
class AiSafeRenderedNote:
    note: dict
    targets: list[str]
    filename: str
    markdown: str
    redacted: bool
    redaction_count: int
    scan_summary: dict[str, SensitiveFinding]


@dataclass
class AiSafeExportPlan:
    target: str
    exported_at: str
    rendered_notes: list[AiSafeRenderedNote]
    scan_summary: dict[str, SensitiveFinding]
    warnings: list[str]

    @property
    def note_count(self) -> int:
        return len(self.rendered_notes)

    @property
    def has_sensitive_findings(self) -> bool:
        return has_sensitive_findings(self.scan_summary)


@dataclass
class AiSafeExportResult:
    package_dir: Path
    manifest_path: Path
    note_count: int


class AiSafePackageExporter:
    def __init__(
        self,
        *,
        database_path: Path = DATABASE_PATH,
        output_root: Path = AI_READBOX_DIR,
        redaction_rules_path: Path = REDACTION_RULES_PATH,
    ) -> None:
        self.database_path = database_path
        self.output_root = output_root
        self.redaction_rules_path = redaction_rules_path

    def build_plan(self, target: str) -> AiSafeExportPlan:
        notes = list_ai_safe_notes_for_target(target, self.database_path)
        has_redacted_notes = any(
            note.get("ai_visibility") == AI_VISIBILITY_REDACTED for note in notes
        )
        redaction_rules = (
            load_redaction_rules(self.redaction_rules_path)
            if has_redacted_notes
            else None
        )
        warnings = [
            redaction_rules.warning
        ] if redaction_rules and redaction_rules.warning else []
        rendered_notes: list[AiSafeRenderedNote] = []
        scan_results = []

        for index, note in enumerate(notes, start=1):
            targets = get_note_ai_targets(note["id"], self.database_path)
            markdown = self._note_to_ai_markdown(note, targets)
            redacted = note.get("ai_visibility") == AI_VISIBILITY_REDACTED
            redaction_count = 0
            if redacted:
                markdown, redaction_count = apply_redactions(
                    markdown,
                    redaction_rules.replacements if redaction_rules else [],
                )
            scan_summary = scan_sensitive_text(markdown)
            scan_results.append(scan_summary)
            filename = f"{index:03d}_{_safe_filename(note.get('title') or '未命名笔记')}.md"
            rendered_notes.append(
                AiSafeRenderedNote(
                    note=note,
                    targets=targets,
                    filename=filename,
                    markdown=markdown,
                    redacted=redacted,
                    redaction_count=redaction_count,
                    scan_summary=scan_summary,
                )
            )

        return AiSafeExportPlan(
            target=target,
            exported_at=now_shanghai().strftime("%Y-%m-%d %H:%M"),
            rendered_notes=rendered_notes,
            scan_summary=merge_sensitive_findings(scan_results),
            warnings=warnings,
        )

    def write_plan(self, plan: AiSafeExportPlan) -> AiSafeExportResult:
        package_dir = self._package_dir(plan.target)
        notes_dir = package_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=False)
        for rendered_note in plan.rendered_notes:
            (notes_dir / rendered_note.filename).write_text(
                rendered_note.markdown + "\n",
                encoding="utf-8",
            )
        manifest = self._manifest(plan)
        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return AiSafeExportResult(
            package_dir=package_dir,
            manifest_path=manifest_path,
            note_count=plan.note_count,
        )

    def _package_dir(self, target: str) -> Path:
        timestamp = now_shanghai().strftime("%Y-%m-%d_%H%M")
        base = self.output_root / f"{target_slug(target)}_{timestamp}"
        if not base.exists():
            return base
        suffix = now_shanghai().strftime("%S")
        return self.output_root / f"{target_slug(target)}_{timestamp}_{suffix}"

    def _note_to_ai_markdown(self, note: dict, targets: list[str]) -> str:
        tags = parse_tags(note.get("tags", ""))
        tag_text = " ".join(f"#{tag}" for tag in tags) if tags else "无"
        target_text = "、".join(targets) if targets else "未设置"
        directory = " / ".join(
            [
                note.get("main_category", ""),
                note.get("sub_category", ""),
                note.get("sub_sub_category", ""),
            ]
        )
        body = _html_to_markdown(note.get("content_html", ""))
        return "\n".join(
            [
                f"# {note.get('title') or '未命名笔记'}",
                "",
                f"- 目录：{directory}",
                f"- 标签：{tag_text}",
                f"- AI 可见性：{note.get('ai_visibility', '')}",
                f"- AI 适用对象：{target_text}",
                f"- 创建时间：{format_display_datetime(note.get('created_at', ''))}",
                f"- 更新时间：{format_display_datetime(note.get('updated_at', ''))}",
                "",
                "---",
                "",
                body.strip(),
            ]
        ).rstrip()

    def _manifest(self, plan: AiSafeExportPlan) -> dict:
        return {
            "exported_at": plan.exported_at,
            "export_type": "ai_safe",
            "package_version": "s-notes-ai-safe-r1",
            "target": plan.target,
            "target_includes": [AI_TARGET_COMMON, plan.target]
            if plan.target != AI_TARGET_COMMON
            else [AI_TARGET_COMMON],
            "note_count": plan.note_count,
            "warnings": plan.warnings,
            "sensitive_scan": findings_to_manifest(plan.scan_summary),
            "has_suspicious_sensitive_content": plan.has_sensitive_findings,
            "notes": [
                {
                    "id": rendered.note["id"],
                    "title": rendered.note["title"],
                    "file": f"notes/{rendered.filename}",
                    "ai_visibility": rendered.note["ai_visibility"],
                    "ai_targets": rendered.targets,
                    "redacted": rendered.redacted,
                    "redaction_count": rendered.redaction_count,
                    "sensitive_scan": findings_to_manifest(rendered.scan_summary),
                    "has_suspicious_sensitive_content": has_sensitive_findings(
                        rendered.scan_summary
                    ),
                    "created_at": format_display_datetime(rendered.note["created_at"]),
                    "updated_at": format_display_datetime(rendered.note["updated_at"]),
                }
                for rendered in plan.rendered_notes
            ],
        }


def _safe_filename(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[/:*?"<>|\\]+', "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "未命名笔记"
    return cleaned[:max_length].rstrip()


def _html_to_markdown(content_html: str) -> str:
    from src.export_center import html_to_markdown

    return html_to_markdown(content_html)
