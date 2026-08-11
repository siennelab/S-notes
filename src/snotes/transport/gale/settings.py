"""Private JSON settings adapter shared by UI and the standalone helper."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.snotes.application.ports import GaleRuntimeSettings


class JsonGaleRuntimeSettings(GaleRuntimeSettings):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get_bool(self, key: str, default: bool) -> bool:
        value = self._read().get(str(key))
        return value if isinstance(value, bool) else bool(default)

    def set_bool(self, key: str, value: bool) -> None:
        payload = self._read()
        payload[str(key)] = bool(value)
        self._write(payload)

    def get_text(self, key: str, default: str = "") -> str:
        value = self._read().get(str(key))
        return value if isinstance(value, str) else str(default)

    def set_text(self, key: str, value: str) -> None:
        payload = self._read()
        payload[str(key)] = str(value)
        self._write(payload)

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=True, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            try:
                Path(name).unlink()
            except FileNotFoundError:
                pass

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
