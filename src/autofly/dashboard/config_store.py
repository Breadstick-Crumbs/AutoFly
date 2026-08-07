from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from autofly.config import AppConfig, WatchConfig, validate_config_data
from autofly.errors import ConfigError
from autofly.locking import ProcessLock


class ConfigStore:
    """Atomically edit watch definitions while retaining a one-generation backup."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._edit_lock_path = path.with_suffix(path.suffix + ".lock")

    def load(self) -> AppConfig:
        with self._lock:
            return validate_config_data(self._read_raw())

    def save_watch(self, watch: WatchConfig, *, original_id: str | None = None) -> AppConfig:
        with self._lock, ProcessLock(self._edit_lock_path):
            raw = self._read_raw()
            watches = raw.get("watches")
            if not isinstance(watches, list):
                raise ConfigError("Configuration watches must be a list")
            target = original_id or watch.id
            matches = [index for index, item in enumerate(watches) if _watch_id(item) == target]
            if original_id is None and matches:
                raise ConfigError(f"Watch {watch.id!r} already exists")
            if original_id is not None and not matches:
                raise ConfigError(f"Unknown watch ID: {original_id}")
            if original_id != watch.id and any(_watch_id(item) == watch.id for item in watches):
                raise ConfigError(f"Watch {watch.id!r} already exists")
            payload = watch.model_dump(mode="json", by_alias=True, exclude_none=True)
            if matches:
                watches[matches[0]] = payload
            else:
                watches.append(payload)
            validated = validate_config_data(raw)
            self._write_raw(raw)
            return validated

    def set_enabled(self, watch_id: str, enabled: bool) -> AppConfig:
        with self._lock, ProcessLock(self._edit_lock_path):
            raw = self._read_raw()
            watches = raw.get("watches")
            if not isinstance(watches, list):
                raise ConfigError("Configuration watches must be a list")
            for item in watches:
                if _watch_id(item) == watch_id and isinstance(item, dict):
                    item["enabled"] = enabled
                    validated = validate_config_data(raw)
                    self._write_raw(raw)
                    return validated
            raise ConfigError(f"Unknown watch ID: {watch_id}")

    def delete_watch(self, watch_id: str) -> AppConfig:
        with self._lock, ProcessLock(self._edit_lock_path):
            raw = self._read_raw()
            watches = raw.get("watches")
            if not isinstance(watches, list):
                raise ConfigError("Configuration watches must be a list")
            remaining = [item for item in watches if _watch_id(item) != watch_id]
            if len(remaining) == len(watches):
                raise ConfigError(f"Unknown watch ID: {watch_id}")
            raw["watches"] = remaining
            validated = validate_config_data(raw)
            self._write_raw(raw)
            return validated

    def _read_raw(self) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"Cannot read configuration {self.path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"Configuration {self.path} must contain a YAML mapping")
        return raw

    def _write_raw(self, raw: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        if self.path.exists():
            shutil.copy2(self.path, backup)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(raw, handle, sort_keys=False, allow_unicode=True)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                temporary.chmod(self.path.stat().st_mode & 0o777)
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _watch_id(value: object) -> str | None:
    return value.get("id") if isinstance(value, dict) else None
