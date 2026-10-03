import tempfile
import unittest
from pathlib import Path

import pandas as pd

from biotrack_studio.consensus_review_model import (
    CORRECTED,
    CONNECTION_DIFFERENCE,
    NO_ISSUE,
    PRIMARY_UNTRACKED,
    PROBLEM,
    ReviewStatusStore,
    load_review_data,
)


class ConsensusReviewModelTests(unittest.TestCase):
    @staticmethod
    def _events():
        return pd.DataFrame(
            [
                {
                    "event_id": "E00001",
                    "event_type": "tracking",
                    "frame": 0,
                    "mask_label": 1,
                    "object_y": 5,
                    "object_x": 6,
                    "sctrack_successor": "1:2",
                    "trackpy_successor": "END",
                },
                {
                    "event_id": "M00001",
                    "event_type": "mask",
                    "frame": 0,
                    "mask_label": 1,
                    "object_y": 5,
                    "object_x": 6,
                    "severity": "high",
                },
                {
                    "event_id": "M00002",
                    "event_type": "mask",
                    "frame": 1,
                    "mask_label": 2,
                    "object_y": 7,
                    "object_x": 8,
                    "severity": "review",
                },
            ]
        )

    def test_tracking_and_mask_events_load_as_separate_types(self):
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
                        "sctrack_successor": "1:2",
                        "trackpy_successor": "END",
                    }
                ]
            ).to_csv(output / "consensus_events.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "event_id": "M00001",
                        "frame": 0,
                        "mask_label": 1,
                        "object_y": 5,
                        "object_x": 6,
                        "severity": "high",
                        "reason": "low_mask_agreement",
                    }
                ]
            ).to_csv(output / "mask_consensus_events.csv", index=False)

            review = load_review_data(output)

        self.assertIsNotNone(review)
        self.assertEqual(set(review.events["event_type"]), {"tracking", "mask"})
        self.assertEqual(review.tracker_names, ["sctrack", "trackpy"])
        tracking = review.events[review.events["event_type"] == "tracking"]
        self.assertEqual(tracking.iloc[0]["reason"], CONNECTION_DIFFERENCE)

    def test_legacy_tracking_events_are_split_into_review_categories(self):
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
                        "reason": "tracker_disagreement",
                        "sctrack_successor": "ABSENT",
                        "trackpy_successor": "1:2",
                    },
                    {
                        "event_id": "E00002",
                        "frame": 0,
                        "mask_label": 2,
                        "object_y": 7,
                        "object_x": 8,
                        "reason": "tracker_disagreement",
                        "sctrack_successor": "END",
                        "trackpy_successor": "1:3",
                    },
                ]
            ).to_csv(output / "consensus_events.csv", index=False)

            review = load_review_data(output)

        reasons = dict(zip(review.events["event_id"], review.events["reason"]))
        self.assertEqual(reasons["E00001"], PRIMARY_UNTRACKED)
        self.assertEqual(reasons["E00002"], CONNECTION_DIFFERENCE)

    def test_review_classification_statistics_and_csv_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            events = self._events()
            (output / "mask_consensus_settings.json").write_text(
                '{"primary_model": "cellpose_cyto", '
                '"reference_model": "stardist"}',
                encoding="utf-8",
            )
            store = ReviewStatusStore(output)
            store.set_classification("E00001", NO_ISSUE)
            store.set_classification("M00001", PROBLEM)
            store.set_classification("M00002", CORRECTED)

            counts = store.statistics(events)
            report_path, summary_path = store.export_reports(
                events, ["sctrack", "trackpy"]
            )
            report = pd.read_csv(report_path)
            summary = pd.read_csv(summary_path)

        self.assertEqual(counts["initial_flagged"], 3)
        self.assertEqual(counts["to_review"], 0)
        self.assertEqual(counts["problem_found"], 1)
        self.assertEqual(counts["corrected"], 1)
        self.assertEqual(counts["no_issue"], 1)
        self.assertEqual(counts["needs_attention"], 1)
        self.assertEqual(counts["connection_differences"], 1)
        self.assertEqual(counts["primary_untracked_objects"], 0)
        self.assertEqual(counts["high_priority_mask_sites"], 1)
        self.assertEqual(counts["review_priority_mask_sites"], 1)
        self.assertEqual(
            report.loc[report["event_id"] == "M00001", "comparison"].iloc[0],
            "cellpose_cyto vs stardist",
        )
        self.assertEqual(set(summary["scope"]), {"all", "tracking", "mask"})

    def test_legacy_resolved_status_is_loaded_as_no_issue(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            pd.DataFrame(
                [
                    {
                        "event_id": "E00001",
                        "status": "resolved",
                        "updated_at": "2026-08-01T00:00:00+00:00",
                    }
                ]
            ).to_csv(output / "consensus_review_status.csv", index=False)

            store = ReviewStatusStore(output)

            self.assertEqual(store.classification_for("E00001"), NO_ISSUE)
            self.assertEqual(store.resolved_ids(), {"E00001"})


if __name__ == "__main__":
    unittest.main()
