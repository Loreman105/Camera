from __future__ import annotations

import logging
from pathlib import Path
import socket
import threading
import time
import tkinter as tk
from tkinter import filedialog

from .camera import CameraWorker, discover_cameras, discovered_indexes
from .config import load, save
from .recorder import SegmentRecorder
from .storage import StorageMonitor
from .stream_server import StreamServer
from .ui import SecurityUI


class Application:
    def __init__(self, recording_only: bool = False, mode: str | None = None, processor_url: str | None = None):
        logging.basicConfig(filename="security_camera.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        double_check_logger = logging.getLogger("double_check")
        if not double_check_logger.handlers:
            handler = logging.FileHandler("double_check.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            double_check_logger.addHandler(handler)
        double_check_logger.setLevel(logging.INFO)
        double_check_logger.propagate = False
        self.settings_path=Path("settings.json")
        first_run = not self.settings_path.exists()
        self.config=load(self.settings_path)
        self.mode = mode or ("Record" if recording_only else "One computer")
        self.recording_only = self.mode == "Record"
        if self.mode == "Record" and not self.config.processor_server_url:
            self.config.processor_server_url = self._local_processor_url()
        if processor_url:
            self.config.processor_server_url = processor_url
        if first_run:
            self._choose_recordings_dir()
            save(self.config, self.settings_path)
        self.recording_enabled = self.recording_only
        self.discovered = [] if self.mode == "Process" else discover_cameras()
        # A fresh install should work as soon as two cameras are found. Existing
        # explicit assignments are preserved, even if indexes later change.
        available = iter(discovered_indexes(self.discovered))
        for camera in self.config.cameras:
            if camera.device_index is None:
                camera.device_index = next(available, None)
        self.storage=StorageMonitor(Path(self.config.recordings_dir),self.config.minimum_free_percent,self.config.cleanup_target_percent,self.config.auto_cleanup)
        logging.info("Application startup; FFmpeg hardware encoders: %s", SegmentRecorder.ffmpeg_encoders() or "none/fallback")
        self.double_check_active = threading.Event()
        self.workers=[] if self.mode == "Process" else [CameraWorker(c,self.config,self.storage,self.double_check_active, self.recording_only) for c in self.config.cameras]
        for w in self.workers: w.start()
        if self.recording_only:
            self.start_recording()
        self.stream_server = None
        if (self.config.remote_stream_enabled or self.mode == "Record") and self.mode != "Process":
            self.stream_server = StreamServer(
                self.config.remote_stream_host,
                self.config.remote_stream_port,
                self._stream_frames,
                self._remote_recordings,
                self._apply_remote_result,
                Path(self.config.recordings_dir),
            )
            self.stream_server.start()
            logging.info("Remote stream URL: http://%s:%s/", self.config.remote_stream_host, self.stream_server.address[1])
        self.root=tk.Tk(); self.ui=SecurityUI(self.root,self)
        self._storage_housekeeping()

    def _choose_recordings_dir(self):
        selected = filedialog.askdirectory(
            title="Choose where to save recordings",
            initialdir=str(Path.home()),
            mustexist=False,
        )
        if selected:
            self.config.recordings_dir = selected
    def _local_processor_url(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 80))
            address = probe.getsockname()[0]
        except OSError:
            address = "127.0.0.1"
        finally:
            probe.close()
        return f"http://{address}:{self.config.remote_stream_port}"
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
        if getattr(self, "mode", "One computer") == "Process":
            self.process_remote_recordings()
            return 1
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
    def process_remote_recordings(self):
        from process_recordings import process_recordings
        server = self.config.processor_server_url.strip()
        if not server:
            logging.warning("Processing requires a recorder URL in Settings")
            return False
        if getattr(self, "processing_thread", None) and self.processing_thread.is_alive():
            return False
        self.processing_status = "Processing recordings..."
        self.processing_started_at = time.monotonic()
        self.processing_thread = threading.Thread(
            target=self._process_remote_worker, args=(server,), name="remote processor", daemon=True,
        )
        self.processing_thread.start()
        return True
    def _process_remote_worker(self, server):
        try:
            from process_recordings import process_recordings
            process_recordings(server, self.config.processing_devices, status_callback=self._set_processing_status)
            self._set_processing_status("Processing complete")
        except Exception as exc:
            logging.exception("Remote processing failed")
            self._set_processing_status(f"Processing failed: {exc}")
    def _set_processing_status(self, status):
        self.processing_status = status
    def _stream_frames(self):
        frames = []
        for worker in self.workers:
            with worker._lock:
                frames.append((worker.camera.label, worker.latest_frame))
        return frames
    def _remote_recordings(self):
        active_paths = {worker.recorder.path for worker in self.workers if worker.recorder.path is not None}
        root = Path(self.config.recordings_dir)
        return [path for path in root.rglob("*.mp4") if path not in active_paths] if root.exists() else []
    def _apply_remote_result(self, path: Path, detected: bool):
        if not path.exists():
            raise OSError(f"Recording no longer exists: {path}")
        if not detected and path.name.lower().endswith("_4k.mp4"):
            SegmentRecorder.downsize_to_inactive(path, (self.config.low_width, self.config.low_height))
        else:
            SegmentRecorder.move_to_detection_bucket(path, detected)
        logging.info("Remote recording verification complete: %s detected=%s", path, detected)
    def _storage_housekeeping(self):
        active = {w.recorder.path for w in self.workers if w.recorder.path is not None}
        self.storage.cleanup(active)
        self.root.after(30_000, self._storage_housekeeping)
    def close(self):
        self.stop_recording(); self.save_settings()
        if self.stream_server is not None:
            self.stream_server.stop()
        for w in self.workers: w.stop()
        self.root.destroy()
    def run(self): self.root.mainloop()

if __name__ == "__main__": Application().run()
