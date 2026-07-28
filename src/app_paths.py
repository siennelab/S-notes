from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


APP_NAME = "S-notes"
DATA_ROOT_ENV_NAME = "S_NOTES_DATA_ROOT"


class DataRootError(RuntimeError):
    pass


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
    def gale_exports(self) -> Path:
        return self.exports / "gale"

    @property
    def gale_mirror(self) -> Path:
        return self.gale_exports / "gale-mirror.db"

    @property
    def imports(self) -> Path:
        return self.data_root / "imports"

    @property
    def config(self) -> Path:
        return self.data_root / "config"

    @property
    def redaction_rules(self) -> Path:
        return self.config / "local_redaction_rules.json"

    @property
    def app_settings(self) -> Path:
        return self.config / "app_settings.ini"

    @property
    def gale_publish_state(self) -> Path:
        return self.config / "gale_publish_state.json"

    @property
    def gale_publish_lock(self) -> Path:
        return self.config / "gale_publish.lock"


def default_data_root(
    home: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    if DATA_ROOT_ENV_NAME in environment:
        return _validated_override_root(environment[DATA_ROOT_ENV_NAME])
    user_home = Path.home() if home is None else Path(home)
    return user_home / "Library" / "Application Support" / APP_NAME


def app_paths(data_root: Path | None = None) -> AppPaths:
    return AppPaths(default_data_root() if data_root is None else Path(data_root))


def _validated_override_root(raw_value: str) -> Path:
    value = str(raw_value).strip()
    if not value:
        raise DataRootError(f"{DATA_ROOT_ENV_NAME} is set but empty")
    root = Path(value)
    if not root.is_absolute():
        raise DataRootError(f"{DATA_ROOT_ENV_NAME} must be an absolute path")
    if root.is_symlink():
        raise DataRootError(f"{DATA_ROOT_ENV_NAME} must not be a symbolic link")

    existed = root.exists()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not root.is_dir():
            raise DataRootError(
                f"{DATA_ROOT_ENV_NAME} does not name a directory: {root}"
            )
        if not existed:
            root.chmod(0o700)
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".snotes-write-check.",
            dir=root,
        )
        os.close(descriptor)
        Path(probe_name).unlink()
    except DataRootError:
        raise
    except OSError as error:
        raise DataRootError(
            f"{DATA_ROOT_ENV_NAME} is not creatable and writable: {root}"
        ) from error
    return root


DATA_ROOT_OVERRIDDEN = DATA_ROOT_ENV_NAME in os.environ
DEFAULT_PATHS = app_paths()
USER_DATA_ROOT = DEFAULT_PATHS.data_root
DATABASE_PATH = DEFAULT_PATHS.database
BACKUPS_DIR = DEFAULT_PATHS.backups
EXPORTS_ROOT = DEFAULT_PATHS.exports
MARKDOWN_EXPORT_DIR = DEFAULT_PATHS.markdown_exports
JSON_EXPORT_DIR = DEFAULT_PATHS.json_exports
AI_READBOX_DIR = DEFAULT_PATHS.ai_readbox
GALE_EXPORT_DIR = DEFAULT_PATHS.gale_exports
GALE_MIRROR_PATH = DEFAULT_PATHS.gale_mirror
IMPORTS_DIR = DEFAULT_PATHS.imports
CONFIG_DIR = DEFAULT_PATHS.config
REDACTION_RULES_PATH = DEFAULT_PATHS.redaction_rules
APP_SETTINGS_PATH = DEFAULT_PATHS.app_settings
GALE_PUBLISH_STATE_PATH = DEFAULT_PATHS.gale_publish_state
GALE_PUBLISH_LOCK_PATH = DEFAULT_PATHS.gale_publish_lock
