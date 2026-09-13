from __future__ import annotations

import logging


class PersonDetector:
    """YOLO detector. It deliberately receives original capture frames, never 144p frames."""
    def __init__(self, confidence: float):
        self.confidence = confidence
        self.error: str | None = None
        self.model = None
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
            # imgsz is intentionally not supplied: ultralytics receives the 4K source frame.
            result = self.model(frame, classes=[0], conf=self.confidence, verbose=False)[0]
            return len(result.boxes) > 0
        except Exception as exc:
            self.error = f"Detection error: {exc}"
            logging.exception("Detector failure")
            return False
