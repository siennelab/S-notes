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


def load_runtime_config() -> RuntimeConfig:
    data_root_text = (
        os.environ.get("S_NOTES_DATA_ROOT", "").strip()
        or os.environ.get("SNOTES_MCP_DATA_ROOT", "").strip()
    )
    if not data_root_text:
        raise RuntimeConfigurationError("data_root_missing")
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
    return RuntimeConfig(
        data_root=Path(data_root_text).expanduser(),
        actor_target_id=actor_target_id,
        read_only=read_only,
    )
