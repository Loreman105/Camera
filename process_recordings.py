"""Download and classify recordings from a running camera over the LAN."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.request import Request, urlopen

from security_camera.config import load
from security_camera.detector import PersonDetector


PERSON_FRAMES_REQUIRED = 6


def request_json(url: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request) as response:
        return json.loads(response.read())


def processing_devices(value: str | None) -> list[str]:
    value = str(value or "auto").strip().lower()
    if value in {"", "auto"}:
        return ["auto"]
    if value == "all":
        try:
            import torch
            return [str(index) for index in range(torch.cuda.device_count())] or ["cpu"]
        except (ImportError, RuntimeError):
            return ["cpu"]
    return [item.strip() for item in value.split(",") if item.strip()] or ["auto"]


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def process_recordings(server: str, device: str | None = None, sample_every: int = 30,
                       status_callback: Callable[[str], None] | None = None,
                       progress_callback: Callable[[int, int, float], None] | None = None) -> int:
    def report(message: str) -> None:
        print(message)
        if status_callback:
            status_callback(message)

    config = load(Path("settings.json"))
    server = server.rstrip("/")
    processor_id = str(uuid.uuid4())
    devices = processing_devices(device or config.processing_devices)
    registration = request_json(
        f"{server}/api/processors/register", method="POST",
        payload={"id": processor_id, "devices": ",".join(devices)},
    )
    report(f"Connected processors: {registration.get('count', 1)}; GPUs: {', '.join(devices)}")
    heartbeat_stop = threading.Event()

    def send_heartbeat() -> None:
        while not heartbeat_stop.wait(5):
            try:
                request_json(
                    f"{server}/api/processors/register", method="POST",
                    payload={"id": processor_id, "devices": ",".join(devices)},
                )
            except Exception as exc:
                report(f"Processor heartbeat failed: {exc}")

    heartbeat = threading.Thread(target=send_heartbeat, name="processor heartbeat", daemon=True)
    heartbeat.start()
    try:
        recordings = request_json(f"{server}/api/recordings")["recordings"]
        if not recordings:
            report("No completed recordings to process.")
            return 0
        total_recordings = len(recordings)
        started_at = time.monotonic()
        report(f"Queued {total_recordings} active clips; ETA calculating...")
        if progress_callback:
            progress_callback(0, total_recordings, 0)

        completed = 0
        completed_lock = threading.Lock()
        def process_worker(worker_device: str):
            nonlocal completed
            detector = PersonDetector(
                config.person_confidence, worker_device, config.processing_batch_size,
            )
            if not detector.available:
                raise RuntimeError(f"YOLO unavailable on processing device {worker_device}: {detector.error}")
            with tempfile.TemporaryDirectory(prefix="sentinel-processing-") as temporary:
                download_root = Path(temporary)
                while True:
                    claimed = request_json(f"{server}/api/recordings/claim", method="POST", payload={"processor": processor_id})["recording"]
                    if claimed is None:
                        return
                    local_path = download_root / Path(claimed["name"])
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    report(f"Downloading: {claimed['name']}")
                    with urlopen(f"{server}/api/recordings/file/{claimed['id']}") as response, local_path.open("wb") as output:
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                    report(f"Analyzing every frame: {claimed['name']}")
                    last_reported_frame = 0

                    def report_progress(frame_count: int) -> bool:
                        nonlocal last_reported_frame
                        if frame_count - last_reported_frame >= 300:
                            last_reported_frame = frame_count
                            report(f"Analyzing {claimed['name']}: frame {frame_count:,}")
                        return True

                    person_frames = detector.video_person_frame_count(local_path, progress=report_progress)
                    if person_frames is None:
                        raise RuntimeError(f"Unable to analyze recording: {claimed['name']}")
                    detected = person_frames >= PERSON_FRAMES_REQUIRED
                    request_json(f"{server}/api/recordings/result/{claimed['id']}", method="POST",
                                 payload={"detected": detected, "processor": processor_id})
                    with completed_lock:
                        completed += 1
                        progress = completed
                        elapsed = time.monotonic() - started_at
                        rate = progress / max(elapsed, 0.001)
                        remaining = (total_recordings - progress) / rate if rate else 0
                    if progress_callback:
                        progress_callback(progress, total_recordings, remaining)
                    report(
                        f"[{progress}/{total_recordings}] "
                        f"{'active' if detected else 'inactive'} "
                        f"({person_frames} person frames): {claimed['name']} "
                        f"| ETA {format_duration(remaining)}"
                    )

        with ThreadPoolExecutor(max_workers=len(devices), thread_name_prefix="processor-gpu") as executor:
            list(executor.map(process_worker, devices))
        return 0
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process recordings from a LAN camera host")
    parser.add_argument("--server", required=True, help="Camera URL, for example http://192.168.1.50:8765")
    parser.add_argument("--device", default=None, help="YOLO device override, such as cpu or 0")
    parser.add_argument("--sample-every", type=int, default=30, help="Inspect every Nth frame")
    args = parser.parse_args()
    return process_recordings(args.server, args.device, args.sample_every)


if __name__ == "__main__":
    raise SystemExit(main())