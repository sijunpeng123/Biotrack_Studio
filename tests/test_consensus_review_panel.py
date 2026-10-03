import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd

try:
    import napari

    from biotrack_studio.consensus_review_model import (
        CORRECTED,
        NO_ISSUE,
        PROBLEM,
    )
    from biotrack_studio.consensus_review_panel import ConsensusReviewPanel
except ImportError:
    napari = None


class ConsensusReviewPanelTests(unittest.TestCase):
    @unittest.skipIf(napari is None, "napari GUI dependencies are not installed")
    def test_panel_classifies_sites_updates_counts_and_exports_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            pd.DataFrame(
                [
                    {
                        "event_id": "E00001",
                        "frame": 0,
                        "mask_label": 1,
                        "object_y": 5,
                        "object_x": 6,
                        "reason": "connection_difference",
                        "sctrack_successor": "1:2",
                        "trackpy_successor": "END",
                    },
                    {
                        "event_id": "E00002",
                        "frame": 1,
                        "mask_label": 2,
                        "object_y": 7,
                        "object_x": 8,
                        "reason": "connection_difference",
                        "sctrack_successor": "END",
                        "trackpy_successor": "2:3",
                    },
                ]
            ).to_csv(output / "consensus_events.csv", index=False)

            viewer = napari.Viewer(show=False)
            try:
                panel = ConsensusReviewPanel(viewer, output, "Test correction")
                self.assertIn("2 unreviewed", panel.statistics.text())
                self.assertIn("2 connection differences", panel.statistics.text())
                self.assertNotIn("To review", panel.statistics.text())
                self.assertEqual(panel.site_filter.currentText(), "Connection differences")
                self.assertEqual(panel.status_filter.currentText(), "Needs attention")
                self.assertEqual(panel.status_filter.itemText(1), "Unreviewed")

                panel.mark_no_issue()
                self.assertEqual(
                    panel.status_store.classification_for("E00001"), NO_ISSUE
                )
                self.assertIn("1 unreviewed", panel.statistics.text())

                panel.mark_problem()
                self.assertEqual(
                    panel.status_store.classification_for("E00002"), PROBLEM
                )
                self.assertIn("1 problems", panel.statistics.text())

                panel.mark_corrected()
                self.assertEqual(
                    panel.status_store.classification_for("E00002"), CORRECTED
                )
                self.assertTrue(
                    (output / "consensus_review_report.csv").is_file()
                )
                self.assertTrue(
                    (output / "consensus_review_summary.csv").is_file()
                )
                panel.status_filter.setCurrentText("Corrected")
                self.assertEqual(len(panel.events), 1)
                panel.close()

                reopened = ConsensusReviewPanel(viewer, output, "Test correction")
                self.assertEqual(
                    reopened.status_store.classification_for("E00001"), NO_ISSUE
                )
                self.assertEqual(
                    reopened.status_store.classification_for("E00002"), CORRECTED
                )
            finally:
                viewer.close()

    @unittest.skipIf(napari is None, "napari GUI dependencies are not installed")
    def test_panel_filters_tracking_categories_and_review_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            pd.DataFrame(
                [
                    {
                        "event_id": "E00001",
                        "frame": 0,
                        "mask_label": 1,
                        "object_y": 5,
                        "object_x": 6,
                        "reason": "connection_difference",
                        "sctrack_successor": "1:2",
                        "trackpy_successor": "END",
                    },
                    {
                        "event_id": "E00002",
                        "frame": 1,
                        "mask_label": 2,
                        "object_y": 7,
                        "object_x": 8,
                        "reason": "primary_untracked_object",
                        "sctrack_successor": "ABSENT",
                        "trackpy_successor": "2:3",
                    },
                ]
            ).to_csv(output / "consensus_events.csv", index=False)

            viewer = napari.Viewer(show=False)
            try:
                panel = ConsensusReviewPanel(viewer, output, "Test correction")
                self.assertEqual(list(panel.events["event_id"]), ["E00001"])

                panel.site_filter.setCurrentText("Untracked objects")
                self.assertEqual(list(panel.events["event_id"]), ["E00002"])

                panel.mark_problem()
                panel.status_filter.setCurrentText("Problem found")
                self.assertEqual(list(panel.events["event_id"]), ["E00002"])

                panel.status_filter.setCurrentText("Corrected")
                self.assertEqual(len(panel.events), 0)
                self.assertTrue(panel.site_filter.isEnabled())
                self.assertTrue(panel.status_filter.isEnabled())
            finally:
                viewer.close()


if __name__ == "__main__":
    unittest.main()
