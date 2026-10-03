import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile

from biotrack_studio.mask_consensus import (
    AUTOMATIC_REFERENCE,
    MaskConsensusSettings,
    analyze_mask_frame,
    as_time_stack,
    build_mask_assisted_correction,
    choose_reference_model,
    compatibility_report,
)


class MaskConsensusTests(unittest.TestCase):
    def test_single_frame_mask_is_treated_as_one_frame(self):
        mask = np.zeros((8, 8), dtype=np.uint16)
        self.assertEqual(as_time_stack(mask).shape, (1, 8, 8))

    def setUp(self):
        self.settings = MaskConsensusSettings(small_object_ratio=0)

    def test_relabelled_identical_masks_are_compatible(self):
        primary = np.zeros((2, 20, 20), dtype=np.uint16)
        reference = np.zeros_like(primary)
        primary[:, 2:8, 2:8] = 1
        primary[:, 10:17, 10:17] = 2
        reference[:, 2:8, 2:8] = 11
        reference[:, 10:17, 10:17] = 25

        report = compatibility_report(primary, reference, self.settings)

        self.assertTrue(report["compatible"])
        self.assertEqual(report["match_coverage"], 1.0)

    def test_iou_thresholds_create_review_and_high_priority_events(self):
        primary = np.zeros((20, 20), dtype=np.uint16)
        primary[5:9, 5:9] = 1
        moderate = np.zeros_like(primary)
        moderate[5:9, 6:10] = 7
        low = np.zeros_like(primary)
        low[5:9, 8:12] = 8

        review = analyze_mask_frame(
            primary, moderate, self.settings, 0, "stardist"
        )
        high = analyze_mask_frame(
            primary, low, self.settings, 0, "stardist"
        )

        self.assertEqual(review[0]["severity"], "review")
        self.assertAlmostEqual(review[0]["iou"], 0.6)
        self.assertEqual(high[0]["severity"], "high")

    def test_split_and_missing_objects_are_high_priority(self):
        primary = np.zeros((30, 30), dtype=np.uint16)
        reference = np.zeros_like(primary)
        primary[4:12, 4:12] = 1
        reference[4:12, 4:8] = 10
        reference[4:12, 8:12] = 11
        reference[18:24, 18:24] = 12

        events = analyze_mask_frame(
            primary, reference, self.settings, 0, "stardist"
        )
        reasons = {event["reason"] for event in events}

        self.assertIn("possible_split_or_merge", reasons)
        self.assertIn("reference_only_object", reasons)
        self.assertTrue(all(event["severity"] == "high" for event in events))

    def test_automatic_reference_avoids_comparing_stardist_with_itself(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary)
            (result / "BioTrack_Studio_run.json").write_text(
                json.dumps(
                    {
                        "segmentation_method": "stardist",
                        "tracking_method": "sctrack",
                    }
                ),
                encoding="utf-8",
            )
            reference, primary = choose_reference_model(
                result, AUTOMATIC_REFERENCE
            )

        self.assertEqual(primary, "stardist")
        self.assertEqual(reference, "cellpose_cyto")

    def test_automatic_reference_requires_primary_model_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                ValueError, "Select the Mask Reference manually"
            ):
                choose_reference_model(temporary, AUTOMATIC_REFERENCE)

    def test_build_workspace_preserves_primary_mask_and_tracking_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = root / "result"
            result.mkdir()
            image = np.zeros((2, 20, 20), dtype=np.uint8)
            primary = np.zeros((2, 20, 20), dtype=np.uint16)
            reference = np.zeros_like(primary)
            primary[:, 3:9, 3:9] = 1
            reference[:, 3:9, 3:9] = 8
            tifffile.imwrite(result / "image.tif", image)
            tifffile.imwrite(result / "mask.tif", primary)
            reference_path = root / "reference.tif"
            tifffile.imwrite(reference_path, reference)
            pd.DataFrame(
                [
                    {
                        "frame": frame,
                        "trackId": 1,
                        "state": "none",
                        "continuous_label": 1,
                        "Center_of_the_object_0": 5.5,
                        "Center_of_the_object_1": 5.5,
                    }
                    for frame in range(2)
                ]
            ).to_csv(result / "track.csv", index=False)
            (result / "config.yaml").write_text(
                "frame_base: 0\nstateCol: state\n", encoding="ascii"
            )
            (result / "BioTrack_Studio_run.json").write_text(
                json.dumps(
                    {
                        "segmentation_method": "cellpose_cyto",
                        "tracking_method": "sctrack",
                    }
                ),
                encoding="utf-8",
            )
            original = (result / "mask.tif").read_bytes()

            summary = build_mask_assisted_correction(
                result,
                assistance_root=root / "assist",
                reference_model="stardist",
                reference_mask=reference_path,
                settings=self.settings,
            )
            correction = Path(summary["correction_dir"])

            self.assertEqual((result / "mask.tif").read_bytes(), original)
            self.assertEqual(
                tifffile.imread(correction / "mask.tif").tolist(),
                primary.tolist(),
            )
            self.assertTrue(
                (correction / "mask_consensus_events.csv").is_file()
            )
            self.assertTrue((correction / "mask_reference.tif").is_file())
            self.assertTrue(summary["primary_mask_preserved"])


if __name__ == "__main__":
    unittest.main()
