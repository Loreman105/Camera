from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

import cv2


class SegmentRecorder:
    def __init__(self, root: Path, camera_folder: str, segment_seconds: int, low_size: tuple[int, int]):
        self.root, self.camera_folder = root, camera_folder
        self.segment_seconds, self.low_size = segment_seconds, low_size
        self.writer = None
        self.path: Path | None = None
        self.mode: str | None = None
        self.started_at: datetime | None = None
        self.bucket: str | None = None

    @staticmethod
    def _folder_name(detected: bool) -> str:
        return "Active" if detected else "Inactive"

    @staticmethod
    def ffmpeg_encoders() -> list[str]:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5).stdout
            return [x for x in ("h264_nvenc", "h264_qsv", "h264_amf") if x in out]
        except (OSError, subprocess.SubprocessError):
            return []

    def _open(self, frame, mode: str, detected: bool) -> None:
        now = datetime.now()
        bucket = self._folder_name(detected)
        # Keep each camera independent and make the current recording area clear:
        # D:\Recordings\Camera 1 - PTZ\Active\YYYY-MM-DD\HH-MM-SS_*.mp4
        # The same camera can also route to D:\Recordings\Camera 1 - PTZ\Inactive\...
        folder = self.root / self.camera_folder / bucket / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{now.strftime('%H-%M-%S')}_{mode.lower()}.mp4"
        size = (frame.shape[1], frame.shape[0]) if mode == "4K" else self.low_size
        self.writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), 30, size)
        if not self.writer.isOpened():
            self.writer = None
            raise RuntimeError(f"Unable to open video output {self.path}")
        self.mode, self.started_at, self.bucket = mode, now, bucket
        logging.info("Recording started: %s", self.path)

    def write(self, frame, mode: str, detected: bool) -> None:
        rotate = self.started_at and (datetime.now() - self.started_at).total_seconds() >= self.segment_seconds
        bucket = self._folder_name(detected)
        if self.writer is None or mode != self.mode or rotate or bucket != self.bucket:
            self.close()
            self._open(frame, mode, detected)
        # This is the only resize: capture and detector retain the original 4K frame.
        output = frame if mode == "4K" else cv2.resize(frame, self.low_size, interpolation=cv2.INTER_AREA)
        self.writer.write(output)

    def close(self) -> None:
        if self.writer:
            self.writer.release()
            logging.info("Recording stopped: %s", self.path)
        self.writer = self.path = self.started_at = None
        self.mode = None
        self.bucket = None
