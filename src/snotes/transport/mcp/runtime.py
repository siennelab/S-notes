"""Environment-only runtime configuration for the isolated R2 stdio MCP."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class RuntimeConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    data_root: Path
    actor_target_id: str
    read_only: bool
    tool_allowlist: frozenset[str] | None


def load_runtime_config() -> RuntimeConfig:
    from src.app_paths import (
        DATA_ROOT_ENV_NAME,
        DataRootError,
        default_data_root,
    )

    legacy_data_root = os.environ.get("SNOTES_MCP_DATA_ROOT", "").strip()
    try:
        if DATA_ROOT_ENV_NAME in os.environ:
            data_root = default_data_root()
        elif legacy_data_root:
            data_root = default_data_root(
                environ={DATA_ROOT_ENV_NAME: legacy_data_root}
            )
        else:
            data_root = default_data_root(environ={})
    except DataRootError as error:
        raise RuntimeConfigurationError(str(error)) from error
    read_only_text = os.environ.get("SNOTES_MCP_READ_ONLY", "0").strip().lower()
    if read_only_text not in {"0", "1", "false", "true"}:
        raise RuntimeConfigurationError("invalid_read_only_setting")
    read_only = read_only_text in {"1", "true"}
    actor_target_id = (
        "clawbot"
        if read_only
        else os.environ.get("SNOTES_MCP_ACTOR_TARGET_ID", "").strip()
    )
    if not actor_target_id:
        raise RuntimeConfigurationError("actor_target_id_missing")
    tool_allowlist_text = os.environ.get(
        "SNOTES_MCP_TOOL_ALLOWLIST", ""
    ).strip()
    tool_allowlist = (
        frozenset(
            name.strip()
            for name in tool_allowlist_text.split(",")
            if name.strip()
        )
        if tool_allowlist_text
        else None
    )
    return RuntimeConfig(
        data_root=data_root,
        actor_target_id=actor_target_id,
        read_only=read_only,
        tool_allowlist=tool_allowlist,
    )
