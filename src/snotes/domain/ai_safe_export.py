"""Safe export plan/result values shared by application and filesystem adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.snotes.domain.sensitive_scan import SensitiveSummary


@dataclass(frozen=True, slots=True)
class AiSafeRenderedNote:
    note_id: int
    title: str
    filename: str
    markdown: str
    visibility: str
    target_names: tuple[str, ...]
    was_transformed: bool
    transform_count: int
    scan_summary: SensitiveSummary
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AiSafeExportPlan:
    actor_id: str
    actor_name: str
    actor_slug: str
    exported_at: str
    rendered_notes: tuple[AiSafeRenderedNote, ...]
    scan_summary: SensitiveSummary
    warnings: tuple[str, ...] = ()

    @property
    def note_count(self) -> int:
        return len(self.rendered_notes)

    @property
    def has_sensitive_findings(self) -> bool:
        return any(item.count > 0 for item in self.scan_summary)


@dataclass(frozen=True, slots=True)
class AiSafeExportResult:
    package_dir: Path
    manifest_path: Path
    note_count: int


@dataclass(frozen=True, slots=True)
class AiSafeExportOutcome:
    status: str
    plan: AiSafeExportPlan
    result: AiSafeExportResult | None = None
