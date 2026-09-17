from __future__ import annotations

import logging
import shutil
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

    @staticmethod
    def move_to_detection_bucket(path: Path, detected: bool) -> Path:
        """Move a completed recording to the bucket selected by verification."""
        bucket = SegmentRecorder._folder_name(detected)
        if len(path.parents) < 2 or path.parent.parent.name not in ("Active", "Inactive"):
            return path
        destination = path.parent.parent.parent / bucket / path.parent.name / path.name
        if destination == path:
            return path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        shutil.move(str(path), str(destination))
        logging.info("Recording verification moved %s to %s", path, destination)
        return destination

    @staticmethod
    def downsize_to_inactive(path: Path, low_size: tuple[int, int]) -> Path | None:
        """Create a 144p inactive copy, then remove the original active file."""
        if path.parent.parent.name != "Active":
            return None
        destination = path.parent.parent.parent / "Inactive" / path.parent.name / path.name.replace("_4k.mp4", "_low.mp4")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}.tmp.mp4")
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            return None
        fps = capture.get(cv2.CAP_PROP_FPS) or 30
        writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"mp4v"), fps, low_size)
        if not writer.isOpened():
            capture.release()
            return None
        frames = 0
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                writer.write(cv2.resize(frame, low_size, interpolation=cv2.INTER_AREA))
                frames += 1
        finally:
            capture.release()
            writer.release()
        if frames == 0:
            temporary.unlink(missing_ok=True)
            return None
        temporary.replace(destination)
        path.unlink()
        logging.info("Downsized person-free recording %s to %s", path, destination)
        return destination
