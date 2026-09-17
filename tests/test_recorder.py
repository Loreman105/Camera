import unittest
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from security_camera.recorder import SegmentRecorder


class SegmentRecorderFolderRoutingTest(unittest.TestCase):
    def setUp(self):
        # Python 3.14's temporary-directory ACLs can prevent nested test files
        # on Windows. This known, repository-local folder is cleaned per test.
        self.root = Path.cwd() / ".test-recorder-work" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_recording_folder_bucket_is_selected_by_detection_state(self):
        recorder = SegmentRecorder(self.root, "Camera 1 - PTZ", 300, (256, 144))

        self.assertEqual(recorder._folder_name(True), "Active")
        self.assertEqual(recorder._folder_name(False), "Inactive")

    def test_verified_recording_can_move_between_detection_buckets(self):
        source = self.root / "Camera 1 - PTZ" / "Inactive" / "2026-09-16" / "12-00-00_low.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")

        destination = SegmentRecorder.move_to_detection_bucket(source, True)

        self.assertEqual(destination.parent.parent.name, "Active")
        self.assertTrue(destination.exists())
        self.assertFalse(source.exists())

    def test_downsize_to_inactive_ignores_non_4k_files(self):
        source = self.root / "Camera 1 - PTZ" / "Inactive" / "2026-09-16" / "12-00-00_low.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")

        self.assertIsNone(SegmentRecorder.downsize_to_inactive(source, (256, 144)))
        self.assertTrue(source.exists())

    def test_downsize_to_inactive_accepts_an_inactive_4k_source(self):
        source = self.root / "Camera 1 - PTZ" / "Inactive" / "2026-09-16" / "12-00-00_4k.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")
        capture = MagicMock()
        capture.isOpened.return_value = False

        with patch("security_camera.recorder.cv2.VideoCapture", return_value=capture) as open_video:
            self.assertIsNone(SegmentRecorder.downsize_to_inactive(source, (256, 144)))

        open_video.assert_called_once_with(str(source))
        capture.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
