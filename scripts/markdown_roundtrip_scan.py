#!/usr/bin/env python3
"""Read-only inventory of stored Notes against the bundled Milkdown editor.

The only database read is through a mode=ro connection and its temporary SQLite
backup. Markdown remains in memory; the JSON result contains aggregate counts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.markdown_roundtrip_check import check


_FIELDS = "id, content_markdown, revision, updated_at, is_deleted"
_PROBES = (
    {"id": "__append_single_newline_probe__", "markdown": "base\n-end"},
    {"id": "__append_double_newline_probe__", "markdown": "base\n\n-end"},
)
_ESCAPED_MARKDOWN = re.compile(r"\\[#*\[\]]")
_SIZE_OPEN = re.compile(r"(?<!\\):size\[")
_SIZE_BARE = re.compile(r"(?<!\\):size(?!\[)")
_SIZE_ESCAPED = re.compile(r"\\:size")
_SIZE_ENCODED = re.compile(r"&#(?:0*58|x0*3a);size", re.IGNORECASE)


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _notes_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash all note rows, including deleted ones, without retaining body text."""
    digest = hashlib.sha256()
    for row in connection.execute(f"SELECT {_FIELDS} FROM notes ORDER BY id"):
        for value in row:
            encoded = str(value).encode("utf-8") if value is not None else b""
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            digest.update(b"\x01" if value is None else b"\x00")
    return digest.hexdigest()


def _changed_lines(before: str, after: str) -> str:
    """Return only changed lines, so unchanged markup does not drive labels."""
    from difflib import SequenceMatcher

    old_lines, new_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
    chunks = []
    for tag, old_start, old_end, new_start, new_end in SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if tag != "equal":
            chunks.extend(old_lines[old_start:old_end])
            chunks.extend(new_lines[new_start:new_end])
    return "".join(chunks)


def classify_diff(before: str, after: str) -> str:
    """Conservative, exclusive labels for triage; not a Markdown parser."""
    if before == after:
        return "zero_diff"
    if before.rstrip("\n") == after.rstrip("\n"):
        return "trailing_or_final_newline"
    if re.sub(r"\n+", "\n", before.rstrip("\n")) == re.sub(r"\n+", "\n", after.rstrip("\n")):
        return "blank_line_or_paragraph_spacing"
    changed = _changed_lines(before, after)
    if ":size" in changed or "&#58;size" in changed:
        return "size_directive"
    if _ESCAPED_MARKDOWN.search(changed):
        return "markdown_escaping"
    if re.search(r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)", changed):
        return "list_or_heading_normalization"
    if re.search(r"!?\[[^\]]*\]\([^)]*\)|<https?://", changed):
        return "link_normalization"
    return "other"


def _note318_shape(body: str) -> dict[str, int]:
    return {
        "size_directive_open_count": len(_SIZE_OPEN.findall(body)),
        "bare_size_token_count": len(_SIZE_BARE.findall(body)),
        "escaped_size_token_count": len(_SIZE_ESCAPED.findall(body)),
        "encoded_colon_size_count": len(_SIZE_ENCODED.findall(body)),
    }


def scan_database(database_path: Path, checker=check) -> dict:
    """Scan a SQLite backup; the source connection cannot write by construction."""
    database_path = Path(database_path)
    with tempfile.TemporaryDirectory(prefix="snotes-step02a-") as temp_dir:
        temp = Path(temp_dir)
        snapshot_path = temp / "notes-snapshot.db"
        with closing(_read_only_connection(database_path)) as source:
            source.execute("BEGIN")
            before_fingerprint = _notes_fingerprint(source)
            with closing(sqlite3.connect(snapshot_path)) as destination:
                source.backup(destination)
                destination.commit()
            source.rollback()

        snapshot_path.chmod(0o400)
        with closing(_read_only_connection(snapshot_path)) as snapshot:
            if _notes_fingerprint(snapshot) != before_fingerprint:
                raise RuntimeError("Snapshot fingerprint differs from source read transaction")
            rows = list(snapshot.execute(
                "SELECT id, content_markdown FROM notes WHERE is_deleted = 0 ORDER BY id"
            ))

        items = [{"id": note_id, "markdown": body} for note_id, body in rows]
        original_home = os.environ.get("HOME")
        original_platform = os.environ.get("QT_QPA_PLATFORM")
        original_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS")
        try:
            os.environ["HOME"] = str(temp / "home")
            (temp / "home").mkdir()
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
            results = checker(items + list(_PROBES))
        finally:
            for name, original in (
                ("HOME", original_home),
                ("QT_QPA_PLATFORM", original_platform),
                ("QTWEBENGINE_CHROMIUM_FLAGS", original_flags),
            ):
                if original is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = original

        by_id = {item["id"]: item for item in results}
        if len(by_id) != len(items) + len(_PROBES):
            raise RuntimeError("Round-trip checker returned incomplete or duplicate IDs")
        counts: Counter[str] = Counter()
        zero_diff = errors = unstable = overescaped_total = overescaped_diff = 0
        note318 = {"present": False}
        for note_id, body in rows:
            result = by_id[note_id]
            overescaped = bool(_ESCAPED_MARKDOWN.search(body))
            overescaped_total += overescaped
            if "error" in result or not isinstance(result.get("first"), str):
                errors += 1
                continue
            first = result["first"]
            unstable += not result.get("stable", False)
            if body == first:
                zero_diff += 1
            else:
                counts[classify_diff(body, first)] += 1
                overescaped_diff += overescaped
            if note_id == 318:
                note318 = {
                    "present": True,
                    "roundtrip_diff": body != first,
                    "diff_type": classify_diff(body, first),
                    **_note318_shape(body),
                }

        probe_single = by_id[_PROBES[0]["id"]]
        probe_double = by_id[_PROBES[1]["id"]]
        probe_error = any("error" in probe for probe in (probe_single, probe_double))
        if probe_error:
            errors += 1
        with closing(_read_only_connection(database_path)) as source_after:
            after_fingerprint = _notes_fingerprint(source_after)
            source_unchanged = after_fingerprint == before_fingerprint
        return {
            "scanned_notes": len(rows),
            "zero_diff": zero_diff,
            "diff_notes": sum(counts.values()),
            "diff_types": dict(sorted(counts.items())),
            "error_count": errors,
            "unstable_after_second_load": unstable,
            "overescaped": {
                "notes_with_backslash_markup": overescaped_total,
                "roundtrip_diff_notes": overescaped_diff,
            },
            "note318": note318,
            "append_newline_probe": {
                "single_input_zero_diff": not probe_error and _PROBES[0]["markdown"] == probe_single.get("first"),
                "double_input_zero_diff": not probe_error and _PROBES[1]["markdown"] == probe_double.get("first"),
                "outputs_equal": not probe_error and probe_single.get("first") == probe_double.get("first"),
                "single_input_diff_type": (
                    classify_diff(_PROBES[0]["markdown"], probe_single["first"])
                    if not probe_error else None
                ),
                "double_input_diff_type": (
                    classify_diff(_PROBES[1]["markdown"], probe_double["first"])
                    if not probe_error else None
                ),
                "single_second_load_stable": not probe_error and bool(probe_single.get("stable")),
                "double_second_load_stable": not probe_error and bool(probe_double.get("stable")),
            },
            "source_unchanged": source_unchanged,
            "source_notes_sha256_before": before_fingerprint,
            "source_notes_sha256_after": after_fingerprint,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        database_resolved = args.database.resolve(strict=True)
        output_resolved = args.output.resolve()
        if output_resolved in {
            database_resolved,
            Path(str(database_resolved) + "-wal"),
            Path(str(database_resolved) + "-shm"),
        }:
            raise ValueError("Output path overlaps source database")
        report = scan_database(args.database)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if report["error_count"] == 0 and report["source_unchanged"] else 1
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
