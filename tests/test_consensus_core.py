import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile

from biotrack_studio.consensus_core import normalize_track, run_consensus


class ConsensusCoreTests(unittest.TestCase):
    @staticmethod
    def _track_rows(track_ids):
        objects = [
            (0, 1, 3.0, 3.0),
            (1, 10, 3.0, 4.0),
            (2, 100, 3.0, 5.0),
            (0, 2, 8.0, 8.0),
            (1, 20, 8.0, 9.0),
            (2, 200, 8.0, 10.0),
        ]
        return pd.DataFrame(
            [
                {
                    "frame": frame,
                    "trackId": track_id,
                    "state": "none",
                    "continuous_label": label,
                    "Center_of_the_object_0": x,
                    "Center_of_the_object_1": y,
                }
                for (frame, label, y, x), track_id in zip(objects, track_ids)
            ]
        )

    def _workspace(self, root: Path, first_ids, second_ids) -> Path:
        workspace = root / "workspace"
        (workspace / "input").mkdir(parents=True)
        (workspace / "tracks" / "primary").mkdir(parents=True)
        (workspace / "tracks" / "reference").mkdir(parents=True)
        mask = np.zeros((3, 12, 14), dtype=np.uint16)
        for frame, label, y, x in [
            (0, 1, 3, 3),
            (1, 10, 3, 4),
            (2, 100, 3, 5),
            (0, 2, 8, 8),
            (1, 20, 8, 9),
            (2, 200, 8, 10),
        ]:
            mask[frame, y - 1 : y + 2, x - 1 : x + 2] = label
        tifffile.imwrite(
            workspace / "input" / "image.tif",
            np.zeros_like(mask),
            photometric="minisblack",
        )
        tifffile.imwrite(
            workspace / "input" / "mask.tif", mask, photometric="minisblack"
        )
        self._track_rows(first_ids).to_csv(
            workspace / "tracks" / "primary" / "track.csv", index=False
        )
        self._track_rows(second_ids).to_csv(
            workspace / "tracks" / "reference" / "track.csv", index=False
        )
        (workspace / "workspace.json").write_text(
            json.dumps({"trackers": ["primary", "reference"]}),
            encoding="utf-8",
        )
        return workspace

    def test_different_track_ids_with_same_connections_do_not_disagree(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(
                Path(temporary),
                [1, 1, 1, 2, 2, 2],
                [77, 77, 77, 88, 88, 88],
            )
            summary = run_consensus(workspace)
            events = pd.read_csv(workspace / "output" / "consensus_events.csv")

        self.assertEqual(summary["disagreement_sites"], 0)
        self.assertEqual(summary["retained_consensus_links"], 4)
        self.assertTrue(events.empty)

    def test_one_reference_break_creates_one_connection_difference(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(
                Path(temporary),
                [1, 1, 1, 2, 2, 2],
                [77, 99, 99, 88, 88, 88],
            )
            summary = run_consensus(workspace)
            events = pd.read_csv(workspace / "output" / "consensus_events.csv")

        self.assertEqual(summary["disagreement_sites"], 1)
        self.assertEqual(summary["connection_difference_sites"], 1)
        self.assertEqual(summary["primary_untracked_sites"], 0)
        self.assertEqual(events.iloc[0]["reason"], "connection_difference")

    def test_missing_primary_object_is_separate_from_connection_difference(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(
                Path(temporary),
                [1, 1, 1, 2, 2, 2],
                [77, 77, 77, 88, 88, 88],
            )
            primary = workspace / "tracks" / "primary" / "track.csv"
            rows = pd.read_csv(primary)
            rows = rows[~((rows["frame"] == 0) & (rows["continuous_label"] == 1))]
            rows.to_csv(primary, index=False)
            summary = run_consensus(workspace)
            events = pd.read_csv(workspace / "output" / "consensus_events.csv")

        self.assertEqual(summary["primary_untracked_sites"], 1)
        self.assertEqual(summary["connection_difference_sites"], 0)
        self.assertEqual(events.iloc[0]["reason"], "primary_untracked_object")

    def test_amdtrk_unassigned_objects_are_not_a_duplicate_track(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "track.csv"
            rows = self._track_rows([1, 1, 1, 0, 0, 0])
            rows.to_csv(path, index=False)
            mask = np.zeros((3, 12, 14), dtype=np.uint16)
            valid_nodes = set()
            for frame, label, y, x in [
                (0, 1, 3, 3),
                (1, 10, 3, 4),
                (2, 100, 3, 5),
                (0, 2, 8, 8),
                (1, 20, 8, 9),
                (2, 200, 8, 10),
            ]:
                mask[frame, y - 1 : y + 2, x - 1 : x + 2] = label
                valid_nodes.add((frame, label))

            normalized = normalize_track(path, mask, valid_nodes)

        self.assertEqual(len(normalized), 3)
        self.assertEqual(set(normalized["trackId"]), {1})


if __name__ == "__main__":
    unittest.main()
