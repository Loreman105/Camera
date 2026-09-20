from __future__ import annotations

import logging
from pathlib import Path
import threading
import tkinter as tk

from .camera import CameraWorker, discover_cameras, discovered_indexes
from .config import load, save
from .recorder import SegmentRecorder
from .storage import StorageMonitor
from .ui import SecurityUI


class Application:
    def __init__(self):
        logging.basicConfig(filename="security_camera.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        double_check_logger = logging.getLogger("double_check")
        if not double_check_logger.handlers:
            handler = logging.FileHandler("double_check.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            double_check_logger.addHandler(handler)
        double_check_logger.setLevel(logging.INFO)
        double_check_logger.propagate = False
        self.settings_path=Path("settings.json"); self.config=load(self.settings_path)
        self.recording_enabled = False
        self.discovered=discover_cameras()
        # A fresh install should work as soon as two cameras are found. Existing
        # explicit assignments are preserved, even if indexes later change.
        available = iter(discovered_indexes(self.discovered))
        for camera in self.config.cameras:
            if camera.device_index is None:
                camera.device_index = next(available, None)
        self.storage=StorageMonitor(Path(self.config.recordings_dir),self.config.minimum_free_percent,self.config.cleanup_target_percent,self.config.auto_cleanup)
        logging.info("Application startup; FFmpeg hardware encoders: %s", SegmentRecorder.ffmpeg_encoders() or "none/fallback")
        self.double_check_active = threading.Event()
        self.workers=[CameraWorker(c,self.config,self.storage,self.double_check_active) for c in self.config.cameras]
        for w in self.workers: w.start()
        self.root=tk.Tk(); self.ui=SecurityUI(self.root,self)
        self._storage_housekeeping()
    def start_recording(self):
        self.recording_enabled = True
        for w in self.workers: w.set_recording(True)
        if hasattr(self, "ui") and self.ui is not None:
            self.ui.sync_recording_button()
    def stop_recording(self):
        self.recording_enabled = False
        for w in self.workers: w.set_recording(False)
        if hasattr(self, "ui") and self.ui is not None:
            self.ui.sync_recording_button()
    def toggle_recording(self):
        if self.recording_enabled:
            self.stop_recording()
        else:
            self.start_recording()
    def toggle_detection(self): self.config.detection_enabled=not self.config.detection_enabled
    def toggle_clean_data(self):
        running = [worker for worker in self.workers if worker.active_check_running]
        if running:
            # The button represents one root-wide job, so a second click stops
            # every worker that may be participating in it.
            for worker in running:
                worker.check_active_recordings()
            return 0

        # One worker owns the job to prevent two models from double-checking the
        # same file. It covers every camera folder and any other 4K recording
        # below the configured recordings root.
        worker = next((item for item in self.workers if item.detector.available), None)
        if worker is None:
            logging.warning("Double-check unavailable: no YOLO detector is loaded")
            return 0
        active_paths = {item.recorder.path for item in self.workers if item.recorder.path is not None}
        started = worker.check_active_recordings(Path(self.config.recordings_dir), active_paths)
        return int(started)
    # Retained for callers of the original CHECK ACTIVE 4K button action.
    def check_active_recordings(self):
        return Application.toggle_clean_data(self)
    def save_settings(self): save(self.config,self.settings_path)
    def _storage_housekeeping(self):
        active = {w.recorder.path for w in self.workers if w.recorder.path is not None}
        self.storage.cleanup(active)
        self.root.after(30_000, self._storage_housekeeping)
    def close(self):
        self.stop_recording(); self.save_settings()
        for w in self.workers: w.stop()
        self.root.destroy()
    def run(self): self.root.mainloop()

if __name__ == "__main__": Application().run()
