from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


APP_NAME = "S-notes"


@dataclass(frozen=True)
class AppPaths:
    data_root: Path

    @property
    def database(self) -> Path:
        return self.data_root / "s_notes.db"

    @property
    def backups(self) -> Path:
        return self.data_root / "backups"

    @property
    def exports(self) -> Path:
        return self.data_root / "exports"

    @property
    def markdown_exports(self) -> Path:
        return self.exports / "markdown"

    @property
    def json_exports(self) -> Path:
        return self.exports / "json"

    @property
    def ai_readbox(self) -> Path:
        return self.exports / "ai_readbox"

    @property
    def imports(self) -> Path:
        return self.data_root / "imports"

    @property
    def config(self) -> Path:
        return self.data_root / "config"

    @property
    def redaction_rules(self) -> Path:
        return self.config / "local_redaction_rules.json"


def default_data_root(home: Path | None = None) -> Path:
    user_home = Path.home() if home is None else Path(home)
    return user_home / "Library" / "Application Support" / APP_NAME


def app_paths(data_root: Path | None = None) -> AppPaths:
    return AppPaths(default_data_root() if data_root is None else Path(data_root))


DEFAULT_PATHS = app_paths()
USER_DATA_ROOT = DEFAULT_PATHS.data_root
DATABASE_PATH = DEFAULT_PATHS.database
BACKUPS_DIR = DEFAULT_PATHS.backups
EXPORTS_ROOT = DEFAULT_PATHS.exports
MARKDOWN_EXPORT_DIR = DEFAULT_PATHS.markdown_exports
JSON_EXPORT_DIR = DEFAULT_PATHS.json_exports
AI_READBOX_DIR = DEFAULT_PATHS.ai_readbox
IMPORTS_DIR = DEFAULT_PATHS.imports
CONFIG_DIR = DEFAULT_PATHS.config
REDACTION_RULES_PATH = DEFAULT_PATHS.redaction_rules
