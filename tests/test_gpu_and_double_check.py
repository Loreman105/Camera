import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from security_camera.camera import CameraWorker
from security_camera.main import Application
from security_camera.recorder import SegmentRecorder


class NvidiaRecordingTest(unittest.TestCase):
    def test_auto_encoder_starts_ffmpeg_with_nvenc(self):
        frame = np.zeros((4, 6, 3), dtype=np.uint8)
        process = MagicMock()
        process.stdin = MagicMock()
        process.wait.return_value = 0
        with patch.object(SegmentRecorder, "ffmpeg_encoders", return_value=["h264_nvenc"]), patch(
            "security_camera.recorder.subprocess.Popen", return_value=process
        ) as popen, patch("pathlib.Path.mkdir"):
            recorder = SegmentRecorder(Path("recordings"), "Camera", 300, (2, 2), "auto")
            recorder._open(frame, "4K", True)
            recorder.write(frame, "4K", True)
            recorder.close()

        command = popen.call_args.args[0]
        self.assertIn("h264_nvenc", command)
        self.assertIn("-video_size", command)
        process.stdin.write.assert_called_once_with(frame.tobytes())

    def test_cpu_encoder_does_not_probe_nvenc(self):
        recorder = SegmentRecorder(Path("recordings"), "Camera", 300, (2, 2), "cpu")
        with patch.object(SegmentRecorder, "ffmpeg_encoders") as encoders:
            self.assertFalse(recorder._should_use_nvenc())
        encoders.assert_not_called()


class DoubleCheckActionTest(unittest.TestCase):
    def test_second_click_requests_cancellation(self):
        worker = CameraWorker.__new__(CameraWorker)
        worker.active_check_running = True
        worker._active_check_cancel = threading.Event()

        self.assertFalse(worker.check_active_recordings())
        self.assertTrue(worker._active_check_cancel.is_set())

    def test_manual_check_requests_priority_over_background_verification(self):
        worker = CameraWorker.__new__(CameraWorker)
        worker.active_check_running = False
        worker._active_check_cancel = threading.Event()
        worker._active_check_requested = threading.Event()
        worker.detector = SimpleNamespace(available=True)
        worker.active_check_total_frames = 0
        worker.active_check_processed_frames = 0
        worker.active_check_total_files = 0
        worker.active_check_processed_files = 0
        worker.status = SimpleNamespace(error="")
        worker.camera = SimpleNamespace(label="Camera")

        with patch("security_camera.camera.threading.Thread") as thread:
            self.assertTrue(worker.check_active_recordings(Path("recordings")))

        self.assertTrue(worker._active_check_requested.is_set())
        self.assertEqual(worker.status.error, "Double-check waiting for background verification")
        thread.return_value.start.assert_called_once_with()

    def test_application_uses_the_legacy_button_action(self):
        first = MagicMock(return_value=True)
        second = MagicMock()
        app = SimpleNamespace(
            config=SimpleNamespace(recordings_dir="recordings"),
            workers=[
                SimpleNamespace(check_active_recordings=first, active_check_running=False, detector=SimpleNamespace(available=True), recorder=SimpleNamespace(path=None)),
                SimpleNamespace(check_active_recordings=second, active_check_running=False, detector=SimpleNamespace(available=True), recorder=SimpleNamespace(path=None)),
            ],
        )

        self.assertEqual(Application.check_active_recordings(app), 1)
        first.assert_called_once_with(Path("recordings"), set())
        second.assert_not_called()
