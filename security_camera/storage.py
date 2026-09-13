from __future__ import annotations

import logging
import shutil
from pathlib import Path


class StorageMonitor:
    def __init__(self, directory: Path, minimum_free_percent: float, cleanup_target_percent: float, auto_cleanup: bool):
        self.directory, self.minimum, self.target, self.auto_cleanup = directory, minimum_free_percent, cleanup_target_percent, auto_cleanup
        self.last_percent: float | None = None

    def free_percent(self) -> float | None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(self.directory)
            self.last_percent = usage.free * 100 / usage.total
            return self.last_percent
        except OSError as exc:
            logging.warning("Storage unavailable: %s", exc)
            return None

    def may_record(self) -> bool:
        value = self.free_percent()
        return value is not None and value > self.minimum

    def cleanup(self, active_files: set[Path]) -> int:
        if not self.auto_cleanup or self.may_record() or not self.directory.exists():
            return 0
        deleted = 0
        files = sorted((p for p in self.directory.rglob("*.mp4") if p not in active_files), key=lambda p: p.stat().st_mtime)
        for file in files:
            if (self.free_percent() or 0) >= self.target:
                break
            try:
                file.unlink()
                deleted += 1
                logging.warning("Storage cleanup removed %s", file)
            except OSError as exc:
                logging.warning("Could not remove old recording %s: %s", file, exc)
        return deleted
