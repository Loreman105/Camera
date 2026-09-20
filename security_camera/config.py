from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CameraConfig:
    label: str
    device_index: int | None = None


@dataclass
class AppConfig:
    cameras: list[CameraConfig] = field(default_factory=lambda: [CameraConfig("Camera 1 - PTZ"), CameraConfig("Camera 2 - Webcam")])
    recordings_dir: str = r"D:\Recordings"
    person_confidence: float = 0.45
    inference_device: str = "auto"
    # "auto" prefers NVIDIA NVENC when the local FFmpeg supports it.  Set to
    # "cpu" to force OpenCV's portable writer or "nvidia" to request NVENC.
    video_encoder: str = "auto"
    person_frames_required: int = 20
    no_person_frames_required: int = 20
    low_width: int = 256
    low_height: int = 144
    capture_width: int = 3840
    capture_height: int = 2160
    segment_seconds: int = 300
    minimum_free_percent: float = 20.0
    cleanup_target_percent: float = 25.0
    auto_cleanup: bool = True
    detection_enabled: bool = True
    # Camera 1 only: experimental UVC reconnect to trigger the camera's configured
    # "Wakeup Pos" preset. This is not a standard USB PTZ command.
    ptz_usb_wakeup_enabled: bool = True
    ptz_idle_return_seconds: int = 300
    ptz_home_preset: int = 1
    remote_stream_enabled: bool = False
    remote_stream_host: str = "0.0.0.0"
    remote_stream_port: int = 8765
    processor_server_url: str = ""
    processing_devices: str = "auto"
    processing_batch_size: int | str = "auto"


def load(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["cameras"] = [CameraConfig(**item) for item in raw.get("cameras", [])]
        return AppConfig(**raw)
    except (OSError, ValueError, TypeError):
        return AppConfig()


def save(config: AppConfig, path: Path) -> None:
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
