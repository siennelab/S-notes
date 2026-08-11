#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MCP_PYTHON="${SNOTES_MCP_PYTHON:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$MCP_PYTHON" ]]; then
    echo "S-notes MCP startup failed: configured MCP Python is not executable." >&2
    exit 2
fi

cd "$ROOT_DIR"
exec "$MCP_PYTHON" -m src.snotes.transport.mcp.server serve
