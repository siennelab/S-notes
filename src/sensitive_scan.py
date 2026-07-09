from __future__ import annotations

import re
from dataclasses import dataclass, field


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
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    "long_random": re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
}


@dataclass
class SensitiveFinding:
    kind: str
    count: int = 0
    previews: list[str] = field(default_factory=list)


def scan_sensitive_text(text: str) -> dict[str, SensitiveFinding]:
    findings = {
        kind: SensitiveFinding(kind=kind)
        for kind in SCAN_LABELS
    }
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
        finding = findings[kind]
        finding.count = len(matches)
        finding.previews = _unique_previews(kind, [match.group(0) for match in matches])
    return findings


def merge_sensitive_findings(
    scan_results: list[dict[str, SensitiveFinding]]
) -> dict[str, SensitiveFinding]:
    merged = {
        kind: SensitiveFinding(kind=kind)
        for kind in SCAN_LABELS
    }
    for result in scan_results:
        for kind, finding in result.items():
            target = merged[kind]
            target.count += finding.count
            for preview in finding.previews:
                if preview not in target.previews and len(target.previews) < 5:
                    target.previews.append(preview)
    return merged


def has_sensitive_findings(findings: dict[str, SensitiveFinding]) -> bool:
    return any(finding.count > 0 for finding in findings.values())


def findings_to_manifest(findings: dict[str, SensitiveFinding]) -> dict:
    return {
        kind: {
            "label": SCAN_LABELS[kind],
            "count": finding.count,
            "previews": finding.previews,
        }
        for kind, finding in findings.items()
    }


def findings_to_dialog_text(findings: dict[str, SensitiveFinding]) -> str:
    lines = ["发现疑似敏感内容：", ""]
    for kind, label in SCAN_LABELS.items():
        lines.append(f"{label}：{findings[kind].count} 处")
    return "\n".join(lines) + "\n\n是否继续导出？"


def _unique_previews(kind: str, values: list[str]) -> list[str]:
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
        first = value.split(".", 1)[0]
        return f"{first}.xxx.xxx.xxx"
    if kind == "uuid":
        return "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    if kind == "private_key":
        first_line = value.strip().splitlines()[0]
        return first_line
    if len(value) <= 8:
        return "..."
    return f"{value[:4]}...{value[-4:]}"


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < existing[1] and existing[0] < span[1] for existing in spans)
