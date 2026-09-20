import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import numpy as np

from security_camera.camera import CameraWorker
from security_camera.detector import PersonDetector
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
        worker._double_check_active = threading.Event()
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
        self.assertTrue(worker._double_check_active.is_set())
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

    def test_manual_check_scans_all_mp4_recordings_in_active_folders(self):
        active = Path("Camera") / "Active" / "2026-09-20" / "clip_4k.mp4"
        active_low = Path("Camera") / "Active" / "2026-09-20" / "clip_low.mp4"
        inactive = Path("Camera") / "Inactive" / "2026-09-20" / "old_4k.mp4"
        folder = SimpleNamespace(exists=lambda: True, rglob=lambda _pattern: [active, active_low, inactive])
        worker = CameraWorker.__new__(CameraWorker)
        worker._stop = threading.Event()
        worker._active_check_cancel = threading.Event()
        worker._active_check_requested = threading.Event()
        worker._double_check_active = threading.Event()
        worker._verification_lock = threading.Lock()
        worker.detector = SimpleNamespace(video_has_person=MagicMock(return_value=True))
        worker.config = SimpleNamespace(low_width=256, low_height=144)
        worker.status = SimpleNamespace(error="")
        worker.active_check_running = True
        worker.active_check_total_frames = 0
        worker.active_check_processed_frames = 0
        worker.active_check_total_files = 0
        worker.active_check_processed_files = 0

        with patch.object(CameraWorker, "_frame_count", return_value=1):
            worker._check_active_recordings(folder, set())

        self.assertEqual(worker.detector.video_has_person.call_args_list, [
            call(active, sample_every=30, progress=worker._active_check_progress),
            call(active_low, sample_every=30, progress=worker._active_check_progress),
        ])
        self.assertEqual(worker.active_check_total_files, 2)
        self.assertFalse(worker._double_check_active.is_set())


class VerificationBatchTuningTest(unittest.TestCase):
    def setUp(self):
        self.detector = PersonDetector.__new__(PersonDetector)
        self.detector.verification_tuning_batches = 2
        self.detector.maximum_verification_batch_size = 256
        self.detector.current_verification_batch_size = 16
        self.performance = {}
        self.failed_sizes = set()

    def test_increases_batch_when_measured_throughput_improves(self):
        for _ in range(2):
            next_size = self.detector._tune_verification_batch(
                16, 1.0, self.performance, self.failed_sizes,
            )
        self.assertEqual(next_size, 32)

        for _ in range(2):
            next_size = self.detector._tune_verification_batch(
                32, 0.25, self.performance, self.failed_sizes,
            )
        self.assertEqual(next_size, 64)

    def test_reverts_to_previous_batch_when_throughput_degrades(self):
        for _ in range(2):
            next_size = self.detector._tune_verification_batch(
                16, 1.0, self.performance, self.failed_sizes,
            )
        self.assertEqual(next_size, 32)

        for _ in range(2):
            next_size = self.detector._tune_verification_batch(
                32, 2.2, self.performance, self.failed_sizes,
            )
        self.assertEqual(next_size, 16)
        self.assertIn(32, self.failed_sizes)
