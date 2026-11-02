import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile

from biotrack_studio.boundary_review import (
    BoundaryReviewSettings, INTERIOR_END, INTERIOR_START,
    find_boundary_events, object_edge_distances,
)
from biotrack_studio.consensus_core import run_consensus
from biotrack_studio.consensus_assist import build_assisted_correction


def make_fixture(records, frames=5, size=64):
    mask = np.zeros((frames, size, size), dtype=np.uint16)
    rows = []
    for frame, label, tid, y, x, parent in records:
        mask[frame, y:y+3, x:x+3] = label
        rows.append(dict(frame=frame, trackId=tid, state="none", continuous_label=label,
                         mask_label=label, Center_of_the_object_0=x+1,
                         Center_of_the_object_1=y+1, parentTrackId=parent))
    return mask, pd.DataFrame(rows)


class BoundaryReviewTests(unittest.TestCase):
    def test_bbox_border_uses_nearest_pixel_and_threshold_is_inclusive(self):
        mask, rows = make_fixture([(1, 1, 1, 20, 0, 0), (2, 2, 2, 20, 15, 0), (2, 3, 3, 20, 16, 0)])
        self.assertEqual(object_edge_distances(mask)[(1, 1)], 0)
        events, _ = find_boundary_events(mask, rows)
        self.assertEqual([(e["frame"], e["mask_label"]) for e in events], [(2, 3)])

    def test_first_last_frames_and_gap_continuations_not_flagged(self):
        mask, rows = make_fixture([(0, 1, 1, 20, 20, 0), (3, 2, 1, 20, 20, 0), (4, 3, 1, 20, 20, 0)])
        events, _ = find_boundary_events(mask, rows, BoundaryReviewSettings(check_starts=True))
        self.assertEqual(events, [])

    def test_interior_singleton_requests_one_end_and_one_start(self):
        mask, rows = make_fixture([(2, 1, 1, 20, 20, 0)])
        events, _ = find_boundary_events(mask, rows, BoundaryReviewSettings(check_starts=True))
        self.assertEqual({e["reason"] for e in events}, {INTERIOR_END, INTERIOR_START})

    def test_recorded_division_end_and_daughter_starts_excluded(self):
        records = [(0, 1, 1, 20, 20, 0), (1, 1, 1, 20, 20, 0),
                   (2, 2, 2, 20, 20, 1), (4, 2, 2, 20, 20, 1),
                   (2, 3, 3, 30, 30, 1), (4, 3, 3, 30, 30, 1)]
        mask, rows = make_fixture(records)
        events, report = find_boundary_events(mask, rows, BoundaryReviewSettings(check_starts=True))
        self.assertEqual(events, [])
        self.assertEqual(report["recorded_division_ends"], 1)
        self.assertEqual(report["recorded_division_starts"], 2)

    def test_shared_lineage_id_does_not_suppress_endpoints(self):
        mask, rows = make_fixture([(1, 1, 1, 20, 20, 0), (2, 2, 2, 20, 20, 0)])
        rows["lineageId"] = 1
        events, _ = find_boundary_events(mask, rows)
        self.assertEqual(len(events), 2)

    def test_missing_parent_column_is_supported(self):
        mask, rows = make_fixture([(1, 1, 1, 20, 20, 0)])
        events, report = find_boundary_events(mask, rows.drop(columns="parentTrackId"))
        self.assertEqual(len(events), 1)
        self.assertFalse(report["lineage_available"])

    def test_bad_parent_entries_do_not_hide_candidates(self):
        for parent in (999, 2, 1.5, np.nan):
            mask, rows = make_fixture([(1, 1, 1, 20, 20, 0), (2, 2, 2, 20, 20, parent)])
            events, _ = find_boundary_events(mask, rows)
            self.assertEqual(len(events), 2)

    def test_invalid_settings_rejected(self):
        for margin in (-1, float("nan"), float("inf"), True, "15"):
            with self.assertRaises(ValueError):
                BoundaryReviewSettings(edge_margin_px=margin).validate()
        with self.assertRaises(ValueError):
            BoundaryReviewSettings(check_ends="false").validate()

    def _workspace(self, root, reference_tid=1):
        ws = root / "workspace"
        (ws / "input").mkdir(parents=True)
        mask, rows = make_fixture([(0, 1, 1, 20, 20, 0), (1, 1, 1, 20, 20, 0),
                                   (2, 2, 2, 20, 20, 0), (4, 2, 2, 20, 20, 0)])
        tifffile.imwrite(ws / "input/mask.tif", mask, photometric="minisblack")
        tifffile.imwrite(ws / "input/image.tif", np.zeros_like(mask), photometric="minisblack")
        for name in ("primary", "trackpy"):
            folder = ws / "tracks" / name
            folder.mkdir(parents=True)
            data = rows.copy()
            if name == "trackpy" and reference_tid == 1:
                data["trackId"] = 1
            data.drop(columns="mask_label").to_csv(folder / "track.csv", index=False)
        (ws / "workspace.json").write_text(json.dumps({"trackers": ["primary", "trackpy"]}))
        return ws

    def test_agreed_end_flagged_without_changing_consensus_or_primary(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = self._workspace(Path(temp), reference_tid=2)
            original = (ws / "tracks/primary/track.csv").read_bytes()
            old = run_consensus(ws)
            tracks = (ws / "output/track.csv").read_bytes()
            new = run_consensus(ws, boundary_settings=BoundaryReviewSettings())
            events = pd.read_csv(ws / "output/consensus_events.csv")
            self.assertEqual(old["disagreement_sites"], new["disagreement_sites"])
            self.assertEqual(new["interior_track_end_sites"], 1)
            self.assertEqual(events.iloc[0].event_id, "B_END_00001_1")
            self.assertEqual((ws / "output/track.csv").read_bytes(), tracks)
            self.assertEqual((ws / "tracks/primary/track.csv").read_bytes(), original)

    def test_existing_disagreement_not_duplicated_or_renumbered(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = self._workspace(Path(temp))
            run_consensus(ws)
            old = pd.read_csv(ws / "output/consensus_events.csv")
            new = run_consensus(ws, boundary_settings=BoundaryReviewSettings())
            events = pd.read_csv(ws / "output/consensus_events.csv")
            self.assertEqual(new["review_sites"], len(old))
            self.assertEqual(list(events.event_id), list(old.event_id))
            self.assertFalse(events.duplicated(["frame", "mask_label"]).any())

    def test_boundary_id_remains_stable_after_margin_change(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = self._workspace(Path(temp), reference_tid=2)
            run_consensus(ws, boundary_settings=BoundaryReviewSettings(edge_margin_px=0))
            original = pd.read_csv(ws / "output/consensus_events.csv")
            run_consensus(ws, boundary_settings=BoundaryReviewSettings(edge_margin_px=15))
            changed = pd.read_csv(ws / "output/consensus_events.csv")
            self.assertEqual(list(changed.event_id), list(original.event_id))

    def test_both_checks_deduplicate_singleton(self):
        with tempfile.TemporaryDirectory() as temp:
            ws = self._workspace(Path(temp), reference_tid=2)
            # All rows become singleton branches in both trackers.
            for name in ("primary", "trackpy"):
                path = ws / "tracks" / name / "track.csv"
                rows = pd.read_csv(path)
                rows["trackId"] = [1, 2, 3, 4]
                rows.to_csv(path, index=False)
            summary = run_consensus(ws, boundary_settings=BoundaryReviewSettings(check_starts=True))
            events = pd.read_csv(ws / "output/consensus_events.csv")
            self.assertEqual(summary["review_sites"], 4)
            self.assertFalse(events.duplicated(["frame", "mask_label"]).any())

    def test_assistance_copies_original_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ws = self._workspace(root, reference_tid=2)
            result = root / "primary_result"
            result.mkdir()
            import shutil
            for name in ("image.tif", "mask.tif"):
                shutil.copy2(ws / "input" / name, result / name)
            shutil.copy2(ws / "tracks/primary/track.csv", result / "track.csv")
            (result / "config.yaml").write_text("frame_base: 0\n")
            original = (result / "track.csv").read_bytes()
            summary = build_assisted_correction(result, "primary", assistance_root=root / "new_runs",
                                                reference_track=ws / "tracks/trackpy/track.csv",
                                                boundary_settings=BoundaryReviewSettings())
            self.assertEqual(summary["interior_track_end_sites"], 1)
            self.assertEqual((Path(summary["correction_dir"]) / "track.csv").read_bytes(), original)
            self.assertEqual((result / "track.csv").read_bytes(), original)
            self.assertFalse((result / "consensus_assist").exists())


if __name__ == "__main__":
    unittest.main()
