"""Additional review prompts for primary-track endpoints inside a 2D field.

No reference annotations are used. Prompts do not change tracking decisions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd


INTERIOR_END = "interior_track_end"
INTERIOR_START = "interior_track_start"


@dataclass(frozen=True)
class BoundaryReviewSettings:
    check_ends: bool = True
    check_starts: bool = False
    edge_margin_px: float = 15.0
    exclude_recorded_divisions: bool = True

    def validate(self):
        for name in ("check_ends", "check_starts", "exclude_recorded_divisions"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if isinstance(self.edge_margin_px, bool) or not isinstance(self.edge_margin_px, (int, float)):
            raise ValueError("Border margin must be a number in pixels")
        if not math.isfinite(self.edge_margin_px) or self.edge_margin_px < 0:
            raise ValueError("Border margin must be finite and non-negative")

    def to_dict(self):
        self.validate()
        return asdict(self)


def object_edge_distances(mask):
    """Distance from the nearest labelled pixel to the image border (0 at it)."""
    distances = {}
    height, width = mask.shape[-2:]
    for frame, plane in enumerate(mask):
        ys, xs = np.nonzero(plane)
        labels = np.asarray(plane)[ys, xs].astype(np.int64, copy=False)
        if not len(labels):
            continue
        pixel_distances = np.minimum.reduce([ys, xs, height - 1 - ys, width - 1 - xs])
        minimum = np.full(int(labels.max()) + 1, np.inf)
        np.minimum.at(minimum, labels, pixel_distances)
        for label in np.unique(labels):
            distances[(frame, int(label))] = float(minimum[label])
    return distances


def recorded_division_nodes(rows):
    """Exclude only explicit, adjacent parent endings with >=2 daughter starts.

    A shared lineageId alone does not identify a division. Invalid, ambiguous,
    self-referencing or non-adjacent parent entries do not suppress prompts.
    """
    if "parentTrackId" not in rows:
        return set(), set()
    groups = {int(tid): group.sort_values("frame") for tid, group in rows.groupby("trackId")}
    children = {}
    for tid, group in groups.items():
        values = pd.to_numeric(group.parentTrackId, errors="coerce")
        # The adapter writes a constant integer parent per branch.
        if values.isna().any() or len(values.unique()) != 1:
            continue
        value = float(values.iloc[0])
        if not value.is_integer() or value <= 0:
            continue
        parent = int(value)
        if parent in groups and parent != tid:
            end_frame = int(groups[parent].iloc[-1].frame)
            if int(group.iloc[0].frame) == end_frame + 1:
                children.setdefault(parent, []).append(tid)
    ends, starts = set(), set()
    for parent, daughters in children.items():
        if len(daughters) < 2:
            continue
        last = groups[parent].iloc[-1]
        ends.add((int(last.frame), int(last.mask_label)))
        for child in daughters:
            first = groups[child].iloc[0]
            starts.add((int(first.frame), int(first.mask_label)))
    return ends, starts


def find_boundary_events(mask, primary_rows, settings=None, *, edge_distances=None):
    """Find primary endpoints, excluding acquisition boundaries and known divisions.

    Later observations of the same track (including gaps) prevent an end flag.
    The adjustable margin is an exploratory setting, not a fitted optimum.
    """
    settings = settings or BoundaryReviewSettings()
    settings.validate()
    if np.ndim(mask) != 3:
        raise ValueError("Boundary review supports only 2D+t labelled masks")
    required = {"trackId", "frame", "mask_label"}
    if not required <= set(primary_rows):
        raise ValueError("Boundary review requires normalized primary tracks")
    distances = object_edge_distances(mask) if edge_distances is None else edge_distances
    division_ends, division_starts = (
        recorded_division_nodes(primary_rows)
        if settings.exclude_recorded_divisions else (set(), set())
    )
    rows = []
    excluded = {"border_ends": 0, "border_starts": 0,
                "recorded_division_ends": 0, "recorded_division_starts": 0}
    for track_id, group in primary_rows.groupby("trackId", sort=True):
        group = group.sort_values("frame", kind="mergesort")
        for enabled, row, reason, division_nodes, kind in [
            (settings.check_ends, group.iloc[-1], INTERIOR_END, division_ends, "ends"),
            (settings.check_starts, group.iloc[0], INTERIOR_START, division_starts, "starts"),
        ]:
            if not enabled:
                continue
            frame, label = int(row.frame), int(row.mask_label)
            if (reason == INTERIOR_END and frame == len(mask) - 1) or (reason == INTERIOR_START and frame == 0):
                continue
            node = (frame, label)
            distance = distances.get(node)
            if distance is None:
                continue
            if distance <= settings.edge_margin_px:
                excluded[f"border_{kind}"] += 1
                continue
            if node in division_nodes:
                excluded[f"recorded_division_{kind}"] += 1
                continue
            rows.append({"frame": frame, "mask_label": label, "reason": reason,
                         "primary_track_id": int(track_id), "edge_distance_px": distance,
                         "edge_margin_px": float(settings.edge_margin_px)})
    rows.sort(key=lambda r: (r["frame"], r["mask_label"], r["reason"]))
    return rows, {"settings": settings.to_dict(), "lineage_available": "parentTrackId" in primary_rows,
                  "candidate_ends": sum(r["reason"] == INTERIOR_END for r in rows),
                  "candidate_starts": sum(r["reason"] == INTERIOR_START for r in rows), **excluded}
