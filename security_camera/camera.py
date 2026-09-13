from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2

from .config import AppConfig, CameraConfig
from .detector import PersonDetector
from .recorder import SegmentRecorder
from .storage import StorageMonitor


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
    def __init__(self, camera: CameraConfig, config: AppConfig, storage: StorageMonitor):
        self.camera, self.config, self.storage = camera, config, storage
        self.status, self.latest_frame = CameraStatus(), None
        self.detector = PersonDetector(config.person_confidence)
        self.recorder = SegmentRecorder(Path(config.recordings_dir), camera.label, config.segment_seconds, (config.low_width, config.low_height))
        self._stop = threading.Event(); self._record = threading.Event(); self._lock = threading.Lock()
        # Keep one newest original frame: an overloaded model may drop stale work,
        # but never makes capture wait or changes the detector input resolution.
        self._detection_queue: queue.Queue = queue.Queue(maxsize=1)
        self._last_person_seen = time.monotonic()
        self._usb_wakeup_requested = threading.Event()
        self._usb_wakeup_used = False
        self.thread = threading.Thread(target=self._run, name=camera.label, daemon=True)
        self.detector_thread = threading.Thread(target=self._detect_loop, name=f"{camera.label} detector", daemon=True)

    def start(self): self.thread.start(); self.detector_thread.start()
    def set_recording(self, enabled: bool): (self._record.set() if enabled else self._record.clear())
    def stop(self): self._stop.set(); self.thread.join(timeout=3); self.detector_thread.join(timeout=3); self.recorder.close()

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
            if self.config.detection_enabled and self.detector.available:
                self._update_detection(self.detector.has_person(frame))

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
