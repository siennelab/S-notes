#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_EXECUTABLE="$SCRIPT_DIR/../../MacOS/S-notes"

if [[ ! -x "$APP_EXECUTABLE" ]]; then
    echo "S-notes MCP startup failed: packaged App executable is not available." >&2
    exit 2
fi

exec "$APP_EXECUTABLE" --mcp-stdio
