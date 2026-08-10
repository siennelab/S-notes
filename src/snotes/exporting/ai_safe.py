"""Filesystem writer for the frozen-R1 AI-safe package format."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.ai_actor_registry import AI_TARGET_COMMON_ID
from src.snotes.domain.ai_safe_export import (
    AiSafeExportPlan,
    AiSafeExportResult,
)
from src.snotes.domain.sensitive_scan import (
    findings_to_manifest,
    has_sensitive_findings,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class AiSafePackageWriter:
    def __init__(
        self,
        output_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.clock = clock or (lambda: datetime.now(SHANGHAI_TZ))

    def write_plan(self, plan: AiSafeExportPlan) -> AiSafeExportResult:
        package_dir = self._package_dir(plan.actor_slug)
        notes_dir = package_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=False)
        for rendered in plan.rendered_notes:
            (notes_dir / rendered.filename).write_text(
                rendered.markdown + "\n",
                encoding="utf-8",
            )
        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(self._manifest(plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return AiSafeExportResult(package_dir, manifest_path, plan.note_count)

    def _package_dir(self, actor_slug: str) -> Path:
        timestamp = self.clock().strftime("%Y-%m-%d_%H%M")
        base = self.output_root / f"{actor_slug}_{timestamp}"
        if not base.exists():
            return base
        return self.output_root / (
            f"{actor_slug}_{timestamp}_{self.clock().strftime('%S')}"
        )

    @staticmethod
    def _manifest(plan: AiSafeExportPlan) -> dict:
        return {
            "exported_at": plan.exported_at,
            "export_type": "ai_safe",
            "package_version": "s-notes-ai-safe-r1",
            "target": plan.actor_name,
            "target_includes": (
                ["通用"]
                if plan.actor_id == AI_TARGET_COMMON_ID
                else ["通用", plan.actor_name]
            ),
            "note_count": plan.note_count,
            "warnings": list(plan.warnings),
            "sensitive_scan": findings_to_manifest(plan.scan_summary),
            "has_suspicious_sensitive_content": plan.has_sensitive_findings,
            "notes": [
                {
                    "id": rendered.note_id,
                    "title": rendered.title,
                    "file": f"notes/{rendered.filename}",
                    "ai_visibility": rendered.visibility,
                    "ai_visibility_display": (
                        "AI脱敏后可见"
                        if rendered.was_transformed
                        else "AI完整可见"
                    ),
                    "ai_targets": list(rendered.target_names),
                    "redacted": rendered.was_transformed,
                    "redaction_count": rendered.transform_count,
                    "sensitive_scan": findings_to_manifest(
                        rendered.scan_summary
                    ),
                    "has_suspicious_sensitive_content": has_sensitive_findings(
                        rendered.scan_summary
                    ),
                    "created_at": _display_datetime(rendered.created_at),
                    "updated_at": _display_datetime(rendered.updated_at),
                }
                for rendered in plan.rendered_notes
            ],
        }


def _display_datetime(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    return timestamp.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
