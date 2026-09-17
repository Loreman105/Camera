import tempfile
import unittest
from pathlib import Path

from security_camera.recorder import SegmentRecorder


class SegmentRecorderFolderRoutingTest(unittest.TestCase):
    def test_recording_folder_bucket_is_selected_by_detection_state(self):
        root = Path(tempfile.mkdtemp())
        recorder = SegmentRecorder(root, "Camera 1 - PTZ", 300, (256, 144))

        self.assertEqual(recorder._folder_name(True), "Active")
        self.assertEqual(recorder._folder_name(False), "Inactive")

    def test_verified_recording_can_move_between_detection_buckets(self):
        root = Path(tempfile.mkdtemp())
        source = root / "Camera 1 - PTZ" / "Inactive" / "2026-09-16" / "12-00-00_low.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")

        destination = SegmentRecorder.move_to_detection_bucket(source, True)

        self.assertEqual(destination.parent.parent.name, "Active")
        self.assertTrue(destination.exists())
        self.assertFalse(source.exists())

    def test_downsize_to_inactive_ignores_non_active_files(self):
        root = Path(tempfile.mkdtemp())
        source = root / "Camera 1 - PTZ" / "Inactive" / "2026-09-16" / "12-00-00_low.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")

        self.assertIsNone(SegmentRecorder.downsize_to_inactive(source, (256, 144)))
        self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
