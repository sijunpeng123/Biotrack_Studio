import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path

import napari
import pandas as pd
from biotrack_studio.consensus_review_panel import ConsensusReviewPanel
from biotrack_studio.consensus_review_model import NO_ISSUE
from biotrack_studio.widget import make_correction_widget


class BoundaryReviewPanelTests(unittest.TestCase):
    def test_endpoint_queue_navigation_classification_export_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            pd.DataFrame([
                dict(event_id="B00001", frame=1, mask_label=2, object_y=10, object_x=10,
                     reason="interior_track_end", ultrack_successor="END", trackpy_successor="END",
                     edge_distance_px=40, edge_margin_px=15),
                dict(event_id="B00002", frame=2, mask_label=3, object_y=10, object_x=10,
                     reason="interior_track_start", ultrack_successor="3:3", trackpy_successor="3:3",
                     edge_distance_px=39, edge_margin_px=15),
            ]).to_csv(output / "consensus_events.csv", index=False)
            viewer = napari.Viewer(show=False)
            try:
                panel = ConsensusReviewPanel(viewer, output, "Test")
                self.assertEqual(panel.site_filter.currentText(), "All sites")
                self.assertEqual(len(panel.events), 2)
                self.assertIn("1 interior endings", panel.statistics.text())
                self.assertIn("away from the image border", panel.detail.text())
                self.assertNotIn("different", panel.detail.text())
                panel.site_filter.setCurrentText("Interior track starts")
                self.assertEqual(list(panel.events.event_id), ["B00002"])
                self.assertIn("starts", panel.detail.text())
                panel.mark_no_issue()
                self.assertEqual(panel.status_store.classification_for("B00002"), NO_ISSUE)
                report = pd.read_csv(output / "consensus_review_report.csv")
                self.assertTrue(report.comparison.eq("ultrack endpoint check").all())
                panel.close()
                reopened = ConsensusReviewPanel(viewer, output, "Test")
                self.assertEqual(reopened.status_store.classification_for("B00002"), NO_ISSUE)
                self.assertEqual(len(reopened.events), 1)
            finally:
                viewer.close()

    def test_correction_widget_controls_follow_tracking_choice(self):
        viewer = napari.Viewer(show=False)
        try:
            widget = make_correction_widget()
            widget.viewer.bind(viewer)
            widget.show()
            self.assertTrue(widget.check_interior_ends.value)
            self.assertFalse(widget.check_interior_starts.value)
            self.assertEqual(widget.border_margin.value, 15)
            widget.consensus_assistance.value = True
            self.assertTrue(widget.check_interior_ends.visible)
            self.assertTrue(widget.border_margin.visible)
            widget.check_interior_ends.value = False
            self.assertFalse(widget.border_margin.visible)
            widget.check_interior_starts.value = True
            self.assertTrue(widget.border_margin.visible)
            widget.consensus_assistance.value = False
            self.assertFalse(widget.check_interior_ends.visible)
            self.assertFalse(widget.check_interior_starts.visible)
        finally:
            if "widget" in locals():
                widget.close()
            viewer.close()


if __name__ == "__main__":
    unittest.main()
