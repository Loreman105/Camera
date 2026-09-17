from __future__ import annotations

import logging
import threading

import cv2


class PersonDetector:
    """YOLO detector. It deliberately receives original capture frames, never 144p frames."""
    def __init__(self, confidence: float):
        self.confidence = confidence
        self.error: str | None = None
        self.model = None
        self._lock = threading.Lock()
        try:
            from ultralytics import YOLO
            self.model = YOLO("yolo11n.pt")
            logging.info("YOLO person detector loaded")
        except Exception as exc:
            self.error = f"YOLO unavailable: {exc}"
            logging.warning(self.error)

    @property
    def available(self) -> bool:
        return self.model is not None

    def has_person(self, frame) -> bool:
        if not self.model:
            return False
        try:
            result = self._detect(frame)
            return len(result.boxes) > 0
        except Exception as exc:
            self.error = f"Detection error: {exc}"
            logging.exception("Detector failure")
            return False

    def _detect(self, frame):
        # The live and recording verification paths share one model instance.
        with self._lock:
            return self.model(frame, classes=[0], conf=self.confidence, verbose=False)[0]

    def video_has_person(self, path, sample_every: int = 30) -> bool | None:
        """Sample a completed recording; return None when verification is unavailable."""
        if not self.model:
            return None
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            logging.warning("Unable to open recording for verification: %s", path)
            capture.release()
            return None
        try:
            frame_index = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    return False
                if frame_index % max(1, sample_every) == 0:
                    try:
                        result = self._detect(frame)
                    except Exception as exc:
                        self.error = f"Recording verification error: {exc}"
                        logging.exception("Recording verification failure for %s", path)
                        return None
                    if len(result.boxes) > 0:
                        return True
                frame_index += 1
        except Exception as exc:
            self.error = f"Recording verification error: {exc}"
            logging.exception("Recording verification failure for %s", path)
            return False
        finally:
            capture.release()
