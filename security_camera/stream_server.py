from __future__ import annotations

import html
import json
import logging
from pathlib import Path
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import quote, unquote

import cv2


FrameProvider = Callable[[], list[tuple[str, object | None]]]
RecordingProvider = Callable[[], list[Path]]
RecordingResult = Callable[[Path, bool], None]


class StreamServer:
    """Small MJPEG server for viewing the latest camera frames remotely."""

    def __init__(self, host: str, port: int, frame_provider: FrameProvider,
                 recording_provider: RecordingProvider | None = None,
                 recording_result: RecordingResult | None = None,
                 recordings_root: Path | None = None):
        self._frame_provider = frame_provider
        self._recording_provider = recording_provider
        self._recording_result = recording_result
        self._recordings_root = recordings_root or Path(".")
        self._coordination_lock = threading.Lock()
        self._claims: dict[str, str] = {}
        self._processors: dict[str, dict] = {}
        handler = self._handler_class()
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="stream server", daemon=True)

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address

    def start(self) -> None:
        self._thread.start()
        logging.info("Remote stream listening on %s:%s", *self.address)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def _handler_class(self):
        stream_server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = unquote(self.path.split("?", 1)[0])
                if path == "/":
                    self._send_page()
                elif path == "/snapshot":
                    self._send_snapshot()
                elif path == "/api/recordings":
                    self._send_recordings()
                elif path == "/api/processors":
                    self._send_processors()
                elif path.startswith("/api/recordings/file/"):
                    self._send_recording(path.removeprefix("/api/recordings/file/"))
                elif path.startswith("/stream/"):
                    self._send_stream(path.removeprefix("/stream/"))
                else:
                    self.send_error(404)

            def do_POST(self):
                path = unquote(self.path.split("?", 1)[0])
                if path.startswith("/api/recordings/result/"):
                    self._receive_result(path.removeprefix("/api/recordings/result/"))
                elif path == "/api/recordings/claim":
                    self._claim_recording()
                elif path == "/api/processors/register":
                    self._register_processor()
                else:
                    self.send_error(404)

            def _send_page(self):
                cameras = stream_server._frame_provider()
                links = "".join(
                    f'<section><h2>{html.escape(label)}</h2>'
                    f'<img src="/stream/{index}" alt="{html.escape(label)}"></section>'
                    for index, (label, _frame) in enumerate(cameras)
                )
                body = f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sentinel camera</title><style>
body{{background:#0b1018;color:#f2f6fc;font:16px system-ui;margin:1rem}}
main{{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}}
section{{background:#172231;padding:.75rem}} img{{display:block;width:100%;height:auto}}
h1,h2{{font-size:1rem;margin:.25rem 0 .75rem}}
</style></head><body><h1>Sentinel camera</h1><main>{links}</main></body></html>"""
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _send_snapshot(self):
                cameras = stream_server._frame_provider()
                frame = next((frame for _label, frame in cameras if frame is not None), None)
                if frame is None:
                    self.send_error(503, "No camera frame available")
                    return
                self._send_jpeg(frame)

            def _send_recordings(self):
                if stream_server._recording_provider is None:
                    self.send_error(404)
                    return
                recordings = []
                for path in stream_server._recording_provider():
                    relative = path.relative_to(Path(stream_server._recordings_root))
                    identifier = quote(relative.as_posix(), safe="")
                    recordings.append({"id": identifier, "name": relative.as_posix(), "bytes": path.stat().st_size})
                self._send_json({"recordings": recordings})

            def _send_processors(self):
                now = time.monotonic()
                with stream_server._coordination_lock:
                    active = [value for value in stream_server._processors.values() if now - value["last_seen"] < 15]
                self._send_json({"count": len(active), "processors": [
                    {"id": item["id"], "devices": item["devices"]} for item in active
                ]})

            def _read_json(self):
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length))

            def _register_processor(self):
                try:
                    payload = self._read_json()
                    processor_id = str(payload["id"]).strip()
                    devices = str(payload.get("devices", "auto"))
                    if not processor_id:
                        raise ValueError("missing processor id")
                except (ValueError, KeyError, json.JSONDecodeError):
                    self.send_error(400, "Invalid processor registration")
                    return
                with stream_server._coordination_lock:
                    stream_server._processors[processor_id] = {"id": processor_id, "devices": devices, "last_seen": time.monotonic()}
                    count = len(stream_server._processors)
                self._send_json({"ok": True, "count": count})

            def _claim_recording(self):
                try:
                    payload = self._read_json()
                    processor_id = str(payload["processor"]).strip()
                except (ValueError, KeyError, json.JSONDecodeError):
                    self.send_error(400, "Invalid processor claim")
                    return
                paths = stream_server._recording_provider() if stream_server._recording_provider else []
                with stream_server._coordination_lock:
                    stream_server._processors.setdefault(processor_id, {"id": processor_id, "devices": "unknown"})["last_seen"] = time.monotonic()
                    selected = next((path for path in paths if str(path.resolve()) not in stream_server._claims), None)
                    if selected is None:
                        self._send_json({"recording": None})
                        return
                    key = str(selected.resolve())
                    stream_server._claims[key] = processor_id
                relative = selected.relative_to(Path(stream_server._recordings_root))
                identifier = quote(relative.as_posix(), safe="")
                self._send_json({"recording": {"id": identifier, "name": relative.as_posix(), "bytes": selected.stat().st_size}})

            def _send_recording(self, identifier):
                path = self._recording_path(identifier)
                if path is None or not path.is_file():
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                with path.open("rb") as recording:
                    while chunk := recording.read(1024 * 1024):
                        self.wfile.write(chunk)

            def _receive_result(self, identifier):
                if stream_server._recording_result is None:
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    result = json.loads(self.rfile.read(length))
                    detected = result["detected"]
                    if not isinstance(detected, bool):
                        raise ValueError("detected must be a boolean")
                    path = self._recording_path(identifier)
                    if path is None:
                        raise ValueError("invalid recording")
                    stream_server._recording_result(path, detected)
                    with stream_server._coordination_lock:
                        stream_server._claims.pop(str(path.resolve()), None)
                        processor_id = str(result.get("processor", ""))
                        if processor_id in stream_server._processors:
                            stream_server._processors[processor_id]["last_seen"] = time.monotonic()
                except (ValueError, KeyError, json.JSONDecodeError, OSError):
                    self.send_error(400, "Invalid recording result")
                    return
                self._send_json({"ok": True})

            def _recording_path(self, identifier):
                if stream_server._recording_provider is None:
                    return None
                try:
                    requested = (Path(stream_server._recordings_root) / unquote(identifier)).resolve()
                    root = Path(stream_server._recordings_root).resolve()
                    if root not in requested.parents or requested.suffix.lower() != ".mp4":
                        return None
                    return next((path for path in stream_server._recording_provider() if path.resolve() == requested), None)
                except (OSError, RuntimeError):
                    return None

            def _send_json(self, value):
                payload = json.dumps(value).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _send_stream(self, camera_index):
                try:
                    index = int(camera_index)
                except ValueError:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        cameras = stream_server._frame_provider()
                        if index < 0 or index >= len(cameras) or cameras[index][1] is None:
                            time.sleep(0.2)
                            continue
                        ok, encoded = cv2.imencode(".jpg", cameras[index][1])
                        if not ok:
                            time.sleep(0.2)
                            continue
                        payload = encoded.tobytes()
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload + b"\r\n")
                        self.wfile.flush()
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def _send_jpeg(self, frame):
                ok, encoded = cv2.imencode(".jpg", frame)
                if not ok:
                    self.send_error(503, "Unable to encode frame")
                    return
                payload = encoded.tobytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                logging.debug("stream client %s - %s", self.address_string(), format % args)

        return Handler