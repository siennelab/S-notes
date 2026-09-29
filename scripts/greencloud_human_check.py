#!/usr/bin/env python3
"""Manual human gate: publish, then show one common note through both MCP actors."""
from pathlib import Path
import argparse
import asyncio
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from src.actor_mirror_publish import main as publish
from src.app_paths import DEFAULT_PATHS
from src.snotes.storage.connection import SQLiteConnectionFactory

PREVIEW_CHARS = 100


class TitleLookupError(ValueError):
    def __init__(self, code: str, candidates: list[dict] | None = None):
        super().__init__(code)
        self.candidates = candidates or []


def lookup_title(database: Path, title: str) -> int:
    """Script-only lookup: inspect metadata without initializing or changing DB."""
    with SQLiteConnectionFactory(database, read_only=True).read() as connection:
        rows = connection.execute(
            "SELECT id, title, main_category, sub_category, updated_at, ai_visibility "
            "FROM notes WHERE title = ? COLLATE BINARY AND is_deleted = 0 ORDER BY id",
            (title,),
        ).fetchall()
        if not rows:
            raise TitleLookupError("note_not_found")
        if len(rows) != 1:
            raise TitleLookupError("ambiguous_title", [dict(row) for row in rows])
        note = rows[0]
        common = connection.execute(
            "SELECT 1 FROM note_ai_targets WHERE note_id = ? AND target_id = 'common'",
            (note["id"],),
        ).fetchone()
        if not common or note["ai_visibility"] not in ("full", "redacted"):
            raise TitleLookupError("note_not_eligible_common")
        return note["id"]


async def read_note(note_id: int) -> None:
    for user in ("claude", "openclaw"):
        server = StdioServerParameters(
            command="/usr/bin/ssh",
            args=["-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                  "optimal", "runuser", "-u", user, "--", "/usr/local/bin/snotes-cloud-read"],
        )
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool("get_note", {"note_id": note_id})
                payload = result.structuredContent
                if payload is None:
                    payload = json.loads(result.content[0].text)
                print(f"\n{summarize_note(user, payload)}")


def summarize_note(user: str, payload: dict) -> str:
    """Gate summary: metadata plus a short single-line preview, never the full body."""
    lines = [f"actor={user}"]
    for key in ("status", "note_id", "title", "version", "updated_at", "visibility", "reason"):
        if key in payload:
            lines.append(f"{key}={payload[key]}")
    if "safe_text" in payload:
        text = str(payload["safe_text"])
        preview = " ".join(text[:PREVIEW_CHARS].split())
        lines.append(f"safe_text_length={len(text)}")
        lines.append(f"preview={preview}{'…' if len(text) > PREVIEW_CHARS else ''}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("note_id", type=int, nargs="?")
    parser.add_argument("--title", help="Exact title of one active, AI-visible common note")
    args = parser.parse_args(argv)
    if (args.note_id is None) == (args.title is None):
        parser.error("provide either note_id or --title")
    if args.note_id is not None and args.note_id < 1:
        parser.error("note_id must be positive")
    if args.title is not None:
        try:
            args.note_id = lookup_title(DEFAULT_PATHS.database, args.title)
        except TitleLookupError as error:
            print(str(error), file=sys.stderr)
            if error.candidates:
                print(json.dumps(error.candidates, ensure_ascii=False, indent=2), file=sys.stderr)
                print("同名笔记不止一篇：请换一篇标题唯一的 common Note，或用候选 ID 指定。", file=sys.stderr)
            return 2
    status = publish(["publish"])
    if status:
        return status
    asyncio.run(read_note(args.note_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
