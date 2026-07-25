from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from src.app_paths import app_paths
from src.services import ai_access_service


MCP_VERSION = "1.28.1"
DEFAULT_CONFIG_NAME = "mcp_server.json"


class RuntimeConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    data_root: Path
    database_path: Path
    redaction_rules_path: Path
    config_path: Path
    actor_target_id: str
    read_only: bool


def load_runtime_config() -> RuntimeConfig:
    data_root_text = os.environ.get("SNOTES_MCP_DATA_ROOT", "").strip()
    paths = app_paths(Path(data_root_text).expanduser() if data_root_text else None)
    config_path = Path(
        os.environ.get("SNOTES_MCP_CONFIG_PATH", "").strip()
        or paths.config / DEFAULT_CONFIG_NAME
    ).expanduser()
    database_path = Path(
        os.environ.get("SNOTES_MCP_DATABASE_PATH", "").strip() or paths.database
    ).expanduser()
    redaction_rules_path = Path(
        os.environ.get("SNOTES_MCP_REDACTION_RULES_PATH", "").strip()
        or paths.redaction_rules
    ).expanduser()

    actor_target_id = os.environ.get("SNOTES_MCP_ACTOR_TARGET_ID", "").strip()
    if not actor_target_id:
        actor_target_id = _actor_from_config_file(config_path)
    if not actor_target_id:
        raise RuntimeConfigurationError("actor_target_id_missing")

    read_only_text = os.environ.get("SNOTES_MCP_READ_ONLY", "0").strip().lower()
    if read_only_text not in {"0", "1", "false", "true"}:
        raise RuntimeConfigurationError("invalid_read_only_setting")
    return RuntimeConfig(
        data_root=paths.data_root,
        database_path=database_path,
        redaction_rules_path=redaction_rules_path,
        config_path=config_path,
        actor_target_id=actor_target_id,
        read_only=read_only_text in {"1", "true"},
    )


def runtime_health(config: RuntimeConfig) -> dict:
    return ai_access_service.inspect_ai_runtime(
        config.actor_target_id,
        database_path=config.database_path,
    )


def doctor_report(config: RuntimeConfig) -> dict:
    try:
        installed_mcp = metadata.version("mcp")
    except metadata.PackageNotFoundError:
        installed_mcp = "missing"
    health = runtime_health(config)
    python_version = platform.python_version()
    python_major, python_minor, *_ = platform.python_version_tuple()
    python_ok = (python_major, python_minor) == ("3", "13")
    mcp_ok = installed_mcp == MCP_VERSION
    return {
        "status": "ok" if health["status"] == "ok" and python_ok and mcp_ok else "failed",
        "python_version": python_version,
        "python_ok": python_ok,
        "mcp_version": installed_mcp,
        "mcp_ok": mcp_ok,
        "project_import_ok": True,
        "database_path": str(config.database_path),
        "database_exists": health["database_exists"],
        "quick_check": health["quick_check"],
        "schema_version": health["schema_version"],
        "schema_ok": health["schema_ok"],
        "actor_target_status": "ok" if health["actor_ok"] else "unavailable",
        "foreign_keys": health["foreign_keys"],
        "read_only_mode": config.read_only,
        "reason": health["reason"],
    }


def _actor_from_config_file(config_path: Path) -> str:
    if not config_path.is_file():
        return ""
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeConfigurationError("actor_config_invalid") from error
    if not isinstance(payload, dict):
        raise RuntimeConfigurationError("actor_config_invalid")
    value = payload.get("actor_target_id")
    return value.strip() if isinstance(value, str) else ""
