import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from biotrack_studio.widget import (
    SKIP_SEGMENTATION,
    SKIP_TRACKING,
    _write_run_metadata,
    validate_pipeline_output,
    validate_reused_mask,
    validate_tracking_sequence,
)


class WidgetValidationTests(unittest.TestCase):
    def test_sctrack_rejects_sequences_shorter_than_thirteen_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "image.tif"
            tifffile.imwrite(
                image_path,
                np.zeros((12, 8, 8), dtype=np.uint16),
                imagej=True,
            )
            message = validate_tracking_sequence("sctrack", image_path)
            self.assertIn("at least 13 frames", message)

    def test_sctrack_accepts_thirteen_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "image.tif"
            tifffile.imwrite(
                image_path,
                np.zeros((13, 8, 8), dtype=np.uint16),
                imagej=True,
            )
            self.assertIsNone(validate_tracking_sequence("sctrack", image_path))

    def test_other_tracking_methods_are_not_restricted(self):
        self.assertIsNone(validate_tracking_sequence("trackpy", "unused.tif"))

    def test_skip_segmentation_validates_the_existing_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.tif"
            tifffile.imwrite(image_path, np.zeros((3, 8, 9), dtype=np.uint8))
            self.assertIn(
                "requires an existing mask.tif",
                validate_reused_mask(image_path, root),
            )

            tifffile.imwrite(
                root / "mask.tif", np.ones((2, 8, 9), dtype=np.uint16)
            )
            self.assertIn("does not match", validate_reused_mask(image_path, root))

            tifffile.imwrite(
                root / "mask.tif", np.ones((3, 8, 9), dtype=np.uint16)
            )
            self.assertIsNone(validate_reused_mask(image_path, root))

    def test_segmentation_only_does_not_keep_a_stale_track(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "track.csv").write_text(
                "frame,trackId\n", encoding="utf-8"
            )
            message = validate_pipeline_output("stardist", SKIP_TRACKING, root)
            self.assertIn("already contains track.csv", message)
            self.assertIsNone(
                validate_pipeline_output(SKIP_SEGMENTATION, "trackpy", root)
            )

    def test_skip_segmentation_preserves_the_primary_model_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_run_metadata(root, "stardist", SKIP_TRACKING)
            _write_run_metadata(root, SKIP_SEGMENTATION, "trackpy")
            metadata = json.loads(
                (root / "BioTrack_Studio_run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["segmentation_method"], "stardist")
            self.assertEqual(metadata["tracking_method"], "trackpy")


if __name__ == "__main__":
    unittest.main()
