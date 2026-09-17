from __future__ import annotations

import logging
import threading

import cv2


class PersonDetector:
    """YOLO detector. It deliberately receives original capture frames, never 144p frames."""
    def __init__(self, confidence: float, device: str = "auto"):
        self.confidence = confidence
        self.error: str | None = None
        self.model = None
        self.device = self._select_device(device)
        self.use_half = self.device != "cpu"
        self._lock = threading.Lock()
        try:
            from ultralytics import YOLO
            self.model = YOLO("yolo11n.pt")
            self.model.to(self.device)
            logging.info("YOLO person detector loaded on %s%s", self.device, " (FP16)" if self.use_half else "")
        except Exception as exc:
            self.error = f"YOLO unavailable: {exc}"
            logging.warning(self.error)

    @staticmethod
    def _select_device(requested: str) -> str:
        requested = str(requested).strip().lower()
        if requested == "cpu":
            return "cpu"
        try:
            import torch
            if not torch.cuda.is_available():
                logging.info("CUDA unavailable; using CPU for YOLO")
                return "cpu"
            count = torch.cuda.device_count()
            index = 0 if requested == "auto" else int(requested)
            if index < 0 or index >= count:
                raise ValueError(f"GPU index {index} is unavailable")
            logging.info("Using CUDA GPU %s: %s", index, torch.cuda.get_device_name(index))
            return f"cuda:{index}"
        except (ImportError, ValueError, RuntimeError) as exc:
            logging.warning("GPU selection failed (%s); using CPU for YOLO", exc)
            return "cpu"

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
            return self.model(frame, classes=[0], conf=self.confidence, device=self.device, half=self.use_half, verbose=False)[0]

    def video_has_person(self, path, sample_every: int = 30, progress=None) -> bool | None:
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
                if progress is not None and progress(frame_index + 1) is False:
                    return None
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
            return None
        finally:
            capture.release()
