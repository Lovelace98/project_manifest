from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from report_bot.domain import AppPreferences


class PreferencesStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def read(self) -> AppPreferences:
        with self._lock:
            if not self.path.exists():
                return AppPreferences()
            try:
                return AppPreferences.model_validate_json(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return AppPreferences()

    def write(self, preferences: AppPreferences) -> AppPreferences:
        payload = json.dumps(preferences.model_dump(mode="json"), indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".settings-",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            temporary_path.chmod(0o600)
            temporary_path.replace(self.path)
        return preferences
