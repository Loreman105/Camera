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


if __name__ == "__main__":
    unittest.main()
