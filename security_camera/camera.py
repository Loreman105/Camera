from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import psutil

from .config import AppConfig, CameraConfig
from .detector import PersonDetector
from .recorder import SegmentRecorder
from .storage import StorageMonitor


double_check_log = logging.getLogger("double_check")


def discover_cameras(max_index: int = 10) -> list[str]:
    found = []
    for index in range(max_index):
        # Recent OpenCV Windows wheels commonly ship without index-based DSHOW
        # capture. MSMF is the supported Windows path; CAP_ANY is the fallback.
        for backend in (cv2.CAP_MSMF, cv2.CAP_ANY):
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                found.append(f"Camera index {index}")
                cap.release()
                break
            cap.release()
    logging.info("Discovered cameras: %s", found or "none")
    return found


def discovered_indexes(devices: list[str]) -> list[int]:
    """Extract OpenCV indexes from the display strings returned by discovery."""
    indexes = []
    for device in devices:
        try:
            indexes.append(int(device.rsplit(" ", 1)[1]))
        except (IndexError, ValueError):
            continue
    return indexes


@dataclass
class CameraStatus:
    connected: bool = False
    fps: float = 0.0
    person: bool = False
    person_frames: int = 0
    no_person_frames: int = 0
    recording_mode: str = "144p"
    recording_active: bool = False
    error: str = "Waiting"


class CameraWorker:
    def __init__(self, camera: CameraConfig, config: AppConfig, storage: StorageMonitor,
                 double_check_active: threading.Event | None = None):
        self.camera, self.config, self.storage = camera, config, storage
        self.status, self.latest_frame = CameraStatus(), None
        self.detector = PersonDetector(config.person_confidence, config.inference_device)
        self.recorder = SegmentRecorder(
            Path(config.recordings_dir), camera.label, config.segment_seconds,
            (config.low_width, config.low_height), config.video_encoder,
        )
        self._stop = threading.Event(); self._record = threading.Event(); self._lock = threading.Lock()
        self._verification_lock = threading.Lock()
        self._active_check_cancel = threading.Event()
        self._active_check_requested = threading.Event()
        # This event is shared by every camera.  A manual scan gets exclusive
        # use of inference while capture and recording continue normally.
        self._double_check_active = double_check_active or threading.Event()
        self.active_check_running = False
        self.active_check_total_frames = 0
        self.active_check_processed_frames = 0
        self.active_check_total_files = 0
        self.active_check_processed_files = 0
        self.active_check_started_at = 0.0
        self.active_check_processing_started_at = 0.0
        self._double_check_last_metrics_at = 0.0
        # Keep one newest original frame: an overloaded model may drop stale work,
        # but never makes capture wait or changes the detector input resolution.
        self._detection_queue: queue.Queue = queue.Queue(maxsize=1)
        self._last_person_seen = time.monotonic()
        self._usb_wakeup_requested = threading.Event()
        self._usb_wakeup_used = False
        self.thread = threading.Thread(target=self._run, name=camera.label, daemon=True)
        self.detector_thread = threading.Thread(target=self._detect_loop, name=f"{camera.label} detector", daemon=True)
        self.verifier_thread = threading.Thread(target=self._verify_recordings_loop, name=f"{camera.label} verifier", daemon=True)

    def start(self): self.thread.start(); self.detector_thread.start(); self.verifier_thread.start()
    def set_recording(self, enabled: bool): (self._record.set() if enabled else self._record.clear())
    def stop(self):
        self._stop.set(); self.thread.join(timeout=3); self.detector_thread.join(timeout=3); self.verifier_thread.join(timeout=3); self.recorder.close()

    def _update_detection(self, detected: bool) -> None:
        s = self.status
        if detected:
            self._last_person_seen = time.monotonic()
            self._usb_wakeup_used = False
            s.person_frames += 1; s.no_person_frames = 0
            if s.person_frames >= self.config.person_frames_required: s.recording_mode = "4K"
        else:
            s.no_person_frames += 1; s.person_frames = 0
            if s.no_person_frames >= self.config.no_person_frames_required: s.recording_mode = "144p"
        s.person = detected

        # The EZ10H's USB connection is UVC video, not a documented PTZ command
        # transport. Reopening UVC can invoke its camera-side Wakeup Pos preset.
        # Do this only once per no-person period and only for Camera 1 (PTZ).
        is_ptz = self.camera.label.lower().startswith("camera 1")
        idle = time.monotonic() - self._last_person_seen
        if (is_ptz and self.config.ptz_usb_wakeup_enabled and not self._usb_wakeup_used
                and idle >= self.config.ptz_idle_return_seconds):
            self._usb_wakeup_used = True
            self._usb_wakeup_requested.set()
            logging.info("%s idle for %ss; requesting experimental UVC wakeup preset %s", self.camera.label, int(idle), self.config.ptz_home_preset)

    def _detect_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._detection_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if (self.config.detection_enabled and self.detector.available
                    and not self._double_check_active.is_set()):
                self._update_detection(self.detector.has_person(frame))

    def _verify_recordings_loop(self) -> None:
        verified: set[str] = set()
        folder = Path(self.config.recordings_dir) / self.camera.label
        while not self._stop.is_set():
            if self._double_check_active.wait(0.2):
                continue
            with self._verification_lock:
                for path in folder.rglob("*.mp4") if folder.exists() else ():
                    # A manual exhaustive pass takes precedence over routine
                    # sampled verification; release the shared model promptly.
                    if (self._stop.is_set() or self._active_check_requested.is_set()
                            or self._double_check_active.is_set()):
                        break
                    if self.recorder.path == path or path.name in verified:
                        continue
                    detected = self.detector.video_has_person(
                        path, progress=lambda _frame: (
                            not self._active_check_requested.is_set()
                            and not self._double_check_active.is_set()
                        ),
                    )
                    if detected is None:
                        continue
                    try:
                        SegmentRecorder.move_to_detection_bucket(path, detected)
                        verified.add(path.name)
                    except OSError as exc:
                        logging.warning("Unable to update verified recording %s: %s", path, exc)
            self._stop.wait(30)

    def check_active_recordings(self, folder: Path | None = None, excluded_paths: set[Path] | None = None) -> bool:
        """Toggle an exhaustive check of completed MP4 recordings in ``folder``."""
        if self.active_check_running:
            self._active_check_cancel.set()
            return False
        if not self.detector.available:
            logging.warning("Double-check unavailable for %s: %s", self.camera.label, self.detector.error or "YOLO is not loaded")
            return False
        self._active_check_cancel.clear()
        self._active_check_requested.set()
        self._double_check_active.set()
        self.active_check_total_frames = 0
        self.active_check_processed_frames = 0
        self.active_check_total_files = 0
        self.active_check_processed_files = 0
        self.active_check_started_at = time.monotonic()
        self.active_check_processing_started_at = 0.0
        self.active_check_running = True
        self.status.error = "Double-check waiting for background verification"
        root = folder or Path(self.config.recordings_dir) / self.camera.label
        excluded = excluded_paths or set()
        threading.Thread(
            target=self._check_active_recordings, args=(root, excluded),
            name=f"{self.camera.label} active check", daemon=True,
        ).start()
        return True

    @staticmethod
    def _frame_count(path: Path) -> int:
        capture = cv2.VideoCapture(str(path))
        try:
            return max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        finally:
            capture.release()

    def _check_active_recordings(self, folder: Path, excluded_paths: set[Path]) -> None:
        acquired = False
        try:
            while not self._stop.is_set() and not self._active_check_cancel.is_set():
                if self._verification_lock.acquire(timeout=0.2):
                    acquired = True
                    break
            else:
                return
            if self._active_check_cancel.is_set() or self._stop.is_set():
                return
            self._active_check_requested.clear()
            # Only completed recordings in Active folders need a decision.
            # In-progress recordings are excluded by the application.
            paths = [
                path for path in folder.rglob("*.mp4")
                if path.parent.parent.name == "Active" and path not in excluded_paths
            ] if folder.exists() else []
            self.active_check_total_files = len(paths)
            self.status.error = f"Double-checking {len(paths):,} MP4 recordings"
            logging.info("Double-check started for %s: %s completed MP4 recordings", folder, len(paths))
            self.active_check_total_frames = sum(
                (self._frame_count(path) + 29) // 30 for path in paths
            )
            double_check_log.info(
                "START folder=%s files=%s sampled_frames=%s sample_every=30 device=%s",
                folder, len(paths), self.active_check_total_frames, getattr(self.detector, "device", "unknown"),
            )
            for path in paths:
                if self._stop.is_set() or self._active_check_cancel.is_set() or path in excluded_paths:
                    continue
                detected = self.detector.video_has_person(path, sample_every=30, progress=self._active_check_progress)
                if detected is False:
                    try:
                        SegmentRecorder.downsize_to_inactive(path, (self.config.low_width, self.config.low_height))
                    except OSError as exc:
                        logging.warning("Unable to downsize active recording %s: %s", path, exc)
                self.active_check_processed_files += 1
                double_check_log.info(
                    "FILE completed=%s/%s path=%s result=%s batch=%s",
                    self.active_check_processed_files, self.active_check_total_files, path,
                    "person" if detected else "inactive" if detected is False else "unavailable",
                    getattr(self.detector, "current_verification_batch_size", "unknown"),
                )
        finally:
            self._active_check_requested.clear()
            if acquired:
                self._verification_lock.release()
            self.active_check_running = False
            self._double_check_active.clear()
            cancelled = self._active_check_cancel.is_set() or self._stop.is_set()
            state = "cancelled" if cancelled else "complete"
            elapsed = max(0.001, time.monotonic() - getattr(self, "active_check_started_at", time.monotonic()))
            double_check_log.info(
                "END state=%s elapsed=%.1fs files=%s/%s sampled_frames=%s/%s batch=%s",
                state, elapsed, self.active_check_processed_files, self.active_check_total_files,
                self.active_check_processed_frames, self.active_check_total_frames,
                getattr(self.detector, "current_verification_batch_size", "unknown"),
            )
            self.status.error = f"Double-check {state}: {self.active_check_processed_files:,}/{self.active_check_total_files:,} MP4 recordings"
            logging.info("Double-check %s for %s: %s/%s MP4 recordings", state, folder, self.active_check_processed_files, self.active_check_total_files)

    def _active_check_progress(self, processed_frames: int) -> bool:
        self.active_check_processed_frames += 1
        now = time.monotonic()
        if not self.active_check_processing_started_at:
            self.active_check_processing_started_at = now
        last_metrics = getattr(self, "_double_check_last_metrics_at", 0.0)
        if now - last_metrics >= 10.0:
            self._double_check_last_metrics_at = now
            elapsed = max(0.001, now - self.active_check_processing_started_at)
            rate = self.active_check_processed_frames / elapsed
            remaining = max(0, self.active_check_total_frames - self.active_check_processed_frames)
            eta = remaining / rate if rate else 0.0
            try:
                disk = psutil.disk_usage(self.config.recordings_dir)
                disk_text = f"{disk.percent:.1f}%"
            except OSError:
                disk_text = "unavailable"
            double_check_log.info(
                "METRICS files=%s/%s sampled_frames=%s/%s elapsed=%.1fs rate=%.2f_frames_per_sec eta=%.1fs "
                "batch=%s cpu=%.1f%% ram=%.1f%% disk=%s gpu=%.1f%% gpu_memory=%.1f%%",
                self.active_check_processed_files, self.active_check_total_files,
                self.active_check_processed_frames, self.active_check_total_frames, elapsed, rate, eta,
                self.detector.current_verification_batch_size, psutil.cpu_percent(interval=None),
                psutil.virtual_memory().percent, disk_text, self.detector.gpu_utilization_percent(),
                self.detector.gpu_memory_percent(),
            )
        return not self._active_check_cancel.is_set() and not self._stop.is_set()

    def ptz_overlay(self) -> str | None:
        """Human-readable state of the USB wakeup experiment for the live overlay."""
        if not self.camera.label.lower().startswith("camera 1"):
            return None
        if not self.config.ptz_usb_wakeup_enabled:
            return "PTZ HOME: disabled"
        if not self.config.detection_enabled:
            return "PTZ HOME: waiting (detection OFF)"
        if not self.detector.available:
            return "PTZ HOME: waiting (YOLO unavailable)"
        if self._usb_wakeup_used:
            return f"PTZ HOME: UVC reset SENT for preset {self.config.ptz_home_preset} (not confirmed)"
        remaining = max(0, int(self.config.ptz_idle_return_seconds - (time.monotonic() - self._last_person_seen)))
        minutes, seconds = divmod(remaining, 60)
        return f"PTZ HOME: preset {self.config.ptz_home_preset} in {minutes}:{seconds:02d} without a person"

    def _open_capture(self):
        """Use the Windows Media Foundation backend, then OpenCV's fallback."""
        backends = (cv2.CAP_MSMF, cv2.CAP_ANY)
        for backend in backends:
            cap = cv2.VideoCapture(self.camera.device_index, backend)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.capture_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.capture_height)
            cap.set(cv2.CAP_PROP_FPS, 30)
            # MJPEG often enables USB cameras to offer their 4K modes over USB.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            logging.info("%s opened with backend %s", self.camera.label, cap.getBackendName())
            return cap
        return None

    def _run(self) -> None:
        cap = None; last = time.monotonic(); count = 0
        while not self._stop.is_set():
            if self._usb_wakeup_requested.is_set():
                self._usb_wakeup_requested.clear()
                if cap is not None:
                    cap.release()
                    cap = None
                self.status.connected = False
                self.status.error = f"Returning PTZ to UVC wakeup preset {self.config.ptz_home_preset}"
                logging.info("%s UVC feed closed to trigger wakeup preset %s", self.camera.label, self.config.ptz_home_preset)
                # Give the camera time to enter its UVC standby/wakeup cycle.
                if self._stop.wait(2):
                    break
                continue
            if self.camera.device_index is None:
                self.status.error = "No device assigned"; time.sleep(1); continue
            if cap is None or not cap.isOpened():
                cap = self._open_capture()
                if cap is None: self.status.connected = False; self.status.error = f"Could not open index {self.camera.device_index}; retrying"; time.sleep(2); continue
                self.status.connected = True; self.status.error = "Connected"; logging.info("%s connected", self.camera.label)
            ok, frame = cap.read()
            if not ok:
                self.status.connected = False; cap.release(); cap = None; logging.warning("%s disconnected", self.camera.label); continue
            with self._lock: self.latest_frame = frame
            try:
                self._detection_queue.put_nowait(frame)
            except queue.Full:
                # Replace queued stale work with the newest 4K capture frame.
                try: self._detection_queue.get_nowait()
                except queue.Empty: pass
                try: self._detection_queue.put_nowait(frame)
                except queue.Full: pass
            if self._record.is_set() and self.storage.may_record():
                confirmed_detection = self.status.person_frames >= self.config.person_frames_required
                try: self.recorder.write(frame, self.status.recording_mode, confirmed_detection); self.status.recording_active = True
                except Exception as exc: self.status.recording_active = False; self.status.error = str(exc); logging.exception("Recorder error")
            else:
                self.recorder.close(); self.status.recording_active = False
            count += 1; now = time.monotonic()
            if now - last >= 1: self.status.fps = count / (now-last); count = 0; last = now
        if cap: cap.release()
