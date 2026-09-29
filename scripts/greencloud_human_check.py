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
                print(f"\n{user}:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("note_id", type=int)
    args = parser.parse_args()
    if args.note_id < 1:
        parser.error("note_id must be positive")
    status = publish(["publish"])
    if status:
        return status
    asyncio.run(read_note(args.note_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
