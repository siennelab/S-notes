#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${SNOTES_YAN_ALLOW_DATA_ROOT_OVERRIDE:-0}" != "1" ]]; then
    unset S_NOTES_DATA_ROOT
    unset SNOTES_MCP_DATA_ROOT
    unset SNOTES_MCP_DATABASE_PATH
    unset SNOTES_MCP_REDACTION_RULES_PATH
    unset SNOTES_MCP_CONFIG_PATH
fi

export SNOTES_MCP_READ_ONLY=0
export SNOTES_MCP_ACTOR_TARGET_ID=yan
export SNOTES_MCP_TOOL_ALLOWLIST=search_notes,get_note,create_note,append_note,list_reminder_labels,list_reminder_tables,get_reminder_table,list_reminder_items,get_reminder_item,create_reminder_label,create_reminder_table,update_reminder_table,create_reminder_item,update_reminder_item,complete_reminder_item,pause_reminder_item,resume_reminder_item

exec "$SCRIPT_DIR/run_snotes_mcp_stdio.sh"
