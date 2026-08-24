#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${SNOTES_GALE_TUNNEL_ALLOW_DATA_ROOT_OVERRIDE:-0}" != "1" ]]; then
    unset S_NOTES_DATA_ROOT
    unset SNOTES_MCP_DATA_ROOT
    unset SNOTES_MCP_DATABASE_PATH
    unset SNOTES_MCP_REDACTION_RULES_PATH
    unset SNOTES_MCP_CONFIG_PATH
fi

export SNOTES_MCP_READ_ONLY=0
export SNOTES_MCP_ACTOR_TARGET_ID=gale
export SNOTES_MCP_TOOL_ALLOWLIST=search_notes,get_note,list_directories,list_tags,list_ai_metadata_options,create_note_submission

exec "$SCRIPT_DIR/run_snotes_mcp_stdio.sh"
