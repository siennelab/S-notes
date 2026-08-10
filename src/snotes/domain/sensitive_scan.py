"""Frozen-R1 suspicious-content scan over already-safe rendered text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence


SCAN_LABELS = {
    "ip_address": "IP 地址",
    "uuid": "UUID",
    "api_token": "疑似 API key/token",
    "private_key": "私钥块",
    "long_random": "长随机字符串",
}

PATTERNS = {
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "uuid": re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    ),
    "api_token": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]*(?:api[_-]?key|token)"
        r"[A-Za-z0-9_-]*\s*[:=]\s*[A-Za-z0-9_-]{12,})\b",
        re.IGNORECASE,
    ),
    "private_key": re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    "long_random": re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
}


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    kind: str
    count: int = 0
    previews: tuple[str, ...] = ()


SensitiveSummary = tuple[SensitiveFinding, ...]


def scan_sensitive_text(text: str) -> SensitiveSummary:
    findings: list[SensitiveFinding] = []
    uuid_spans: list[tuple[int, int]] = []
    for kind, pattern in PATTERNS.items():
        matches = list(pattern.finditer(text or ""))
        if kind == "long_random":
            matches = [
                match
                for match in matches
                if not _overlaps(match.span(), uuid_spans)
                and not match.group(0).isdigit()
            ]
        if kind == "uuid":
            uuid_spans = [match.span() for match in matches]
        findings.append(
            SensitiveFinding(
                kind,
                len(matches),
                tuple(_unique_previews(kind, [match.group(0) for match in matches])),
            )
        )
    return tuple(findings)


def merge_sensitive_findings(results: Sequence[SensitiveSummary]) -> SensitiveSummary:
    merged: dict[str, SensitiveFinding] = {
        kind: SensitiveFinding(kind) for kind in SCAN_LABELS
    }
    for result in results:
        for finding in result:
            current = merged[finding.kind]
            previews = list(current.previews)
            for preview in finding.previews:
                if preview not in previews and len(previews) < 5:
                    previews.append(preview)
            merged[finding.kind] = SensitiveFinding(
                finding.kind,
                current.count + finding.count,
                tuple(previews),
            )
    return tuple(merged[kind] for kind in SCAN_LABELS)


def has_sensitive_findings(findings: SensitiveSummary) -> bool:
    return any(finding.count > 0 for finding in findings)


def findings_to_manifest(findings: SensitiveSummary) -> dict[str, dict]:
    by_kind: Mapping[str, SensitiveFinding] = {
        finding.kind: finding for finding in findings
    }
    return {
        kind: {
            "label": label,
            "count": by_kind[kind].count,
            "previews": list(by_kind[kind].previews),
        }
        for kind, label in SCAN_LABELS.items()
    }


def findings_to_dialog_text(findings: SensitiveSummary) -> str:
    by_kind = {finding.kind: finding for finding in findings}
    lines = ["发现疑似敏感内容：", ""]
    for kind, label in SCAN_LABELS.items():
        lines.append(f"{label}：{by_kind[kind].count} 处")
    return "\n".join(lines) + "\n\n是否继续导出？"


def _unique_previews(kind: str, values: Sequence[str]) -> list[str]:
    previews: list[str] = []
    for value in values:
        preview = _mask_value(kind, value)
        if preview not in previews:
            previews.append(preview)
        if len(previews) >= 5:
            break
    return previews


def _mask_value(kind: str, value: str) -> str:
    if kind == "ip_address":
        return f"{value.split('.', 1)[0]}.xxx.xxx.xxx"
    if kind == "uuid":
        return "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    if kind == "private_key":
        return value.strip().splitlines()[0]
    if len(value) <= 8:
        return "..."
    return f"{value[:4]}...{value[-4:]}"


def _overlaps(span: tuple[int, int], spans: Sequence[tuple[int, int]]) -> bool:
    return any(span[0] < existing[1] and existing[0] < span[1] for existing in spans)
