"""Generate strict consensus tracks from two or more completed tracking results."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile


STANDARD_COLUMNS = [
    "frame",
    "trackId",
    "state",
    "continuous_label",
    "Center_of_the_object_0",
    "Center_of_the_object_1",
]
ALIASES = {
    "track_id": "trackId",
    "particle": "trackId",
    "t": "frame",
    "original_cell_id": "continuous_label",
    "label": "continuous_label",
    "x": "Center_of_the_object_0",
    "y": "Center_of_the_object_1",
}


def object_stats(mask) -> dict[tuple[int, int], tuple[int, float, float]]:
    stats = {}
    for frame in range(int(mask.shape[0])):
        plane = np.asarray(mask[frame])
        ys, xs = np.nonzero(plane)
        if not len(ys):
            continue
        labels = plane[ys, xs].astype(np.int64, copy=False)
        count = np.bincount(labels)
        sum_y = np.bincount(labels, weights=ys)
        sum_x = np.bincount(labels, weights=xs)
        for label in np.flatnonzero(count):
            if label:
                stats[(frame, int(label))] = (
                    int(count[label]),
                    float(sum_y[label] / count[label]),
                    float(sum_x[label] / count[label]),
                )
    return stats


def normalize_track(path: Path, mask, valid_nodes: set[tuple[int, int]]) -> pd.DataFrame:
    data = pd.read_csv(path)
    data = data.rename(
        columns={source: target for source, target in ALIASES.items() if source in data.columns}
    )
    required = [
        "frame",
        "trackId",
        "Center_of_the_object_0",
        "Center_of_the_object_1",
    ]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")

    numeric = required + (["continuous_label"] if "continuous_label" in data.columns else [])
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=required).copy()
    data["frame"] = data["frame"].astype(int)
    data["trackId"] = data["trackId"].astype(int)
    # napari-amdtrk uses trackId 0 for segmented objects that are not assigned
    # to a track. They remain untracked objects, not one duplicated trajectory.
    data = data[data["trackId"] > 0].copy()
    if data.duplicated(["trackId", "frame"]).any():
        raise ValueError(f"{path} contains duplicate rows for the same trackId and frame")

    max_frame, max_y, max_x = mask.shape[0] - 1, mask.shape[-2] - 1, mask.shape[-1] - 1
    labels = []
    distances = []
    stats = object_stats(mask)
    for row in data.itertuples():
        frame = int(row.frame)
        if frame < 0 or frame > max_frame:
            labels.append(0)
            distances.append(np.nan)
            continue
        supplied = int(row.continuous_label) if hasattr(row, "continuous_label") and pd.notna(row.continuous_label) else 0
        if (frame, supplied) in valid_nodes:
            label = supplied
        else:
            x = min(max(int(round(float(row.Center_of_the_object_0))), 0), max_x)
            y = min(max(int(round(float(row.Center_of_the_object_1))), 0), max_y)
            label = int(mask[frame, y, x])
        labels.append(label)
        item = stats.get((frame, label))
        distances.append(
            float(np.hypot(float(row.Center_of_the_object_1) - item[1], float(row.Center_of_the_object_0) - item[2]))
            if item else np.nan
        )

    data["mask_label"] = labels
    data["mapping_distance"] = distances
    data = data[data["mask_label"] > 0].copy()
    data["_order"] = np.arange(len(data))
    data = data.sort_values(
        ["frame", "mask_label", "mapping_distance", "_order"],
        na_position="last",
        kind="mergesort",
    ).drop_duplicates(["frame", "mask_label"], keep="first")
    return data.sort_values(["trackId", "frame"], kind="mergesort").drop(columns="_order")


def successors(rows: pd.DataFrame) -> tuple[set[tuple[int, int]], dict[tuple[int, int], tuple[int, int]]]:
    present = set(zip(rows["frame"].astype(int), rows["mask_label"].astype(int)))
    outgoing = {}
    for _, group in rows.groupby("trackId", sort=False):
        records = list(group.sort_values("frame", kind="mergesort").itertuples())
        for source, target in zip(records, records[1:]):
            source_node = (int(source.frame), int(source.mask_label))
            target_node = (int(target.frame), int(target.mask_label))
            if target_node[0] > source_node[0]:
                outgoing[source_node] = target_node
    return present, outgoing


def signature(node, present, outgoing):
    if node not in present:
        return "ABSENT"
    target = outgoing.get(node)
    return "END" if target is None else f"{target[0]}:{target[1]}"


def assign_track_ids(
    nodes: list[tuple[int, int]], accepted_edges: dict[tuple[int, int], tuple[int, int]]
) -> dict[tuple[int, int], int]:
    predecessor = {target: source for source, target in accepted_edges.items()}
    assigned = {}
    next_id = 1
    for node in nodes:
        if node in assigned or node in predecessor:
            continue
        current = node
        while current is not None and current not in assigned:
            assigned[current] = next_id
            current = accepted_edges.get(current)
        next_id += 1
    for node in nodes:
        if node not in assigned:
            assigned[node] = next_id
            next_id += 1
    return assigned


def link_or_copy(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    shutil.copy2(source, destination)


def run_consensus(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    input_dir = workspace / "input"
    image_path = input_dir / "image.tif"
    mask_path = input_dir / "mask.tif"
    for path in (image_path, mask_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing required input: {path}")

    manifest_path = workspace / "workspace.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tracker_names = manifest.get("trackers", [])
        if not isinstance(tracker_names, list) or not all(
            isinstance(name, str) and name for name in tracker_names
        ):
            raise ValueError(f"Invalid tracker list in {manifest_path}")
        if len(set(tracker_names)) != len(tracker_names):
            raise ValueError(f"Duplicate tracker names in {manifest_path}")
        track_paths = [workspace / "tracks" / name / "track.csv" for name in tracker_names]
    else:
        track_paths = sorted((workspace / "tracks").glob("*/track.csv"))
        tracker_names = [path.parent.name for path in track_paths]
    if len(track_paths) < 2:
        raise ValueError("Consensus requires at least two tracks/<name>/track.csv files")
    missing_tracks = [str(path) for path in track_paths if not path.is_file()]
    if missing_tracks:
        raise FileNotFoundError("Missing tracking input(s): " + ", ".join(missing_tracks))

    mask = tifffile.memmap(mask_path)
    if mask.ndim != 3:
        raise ValueError(f"Only 2D+t mask stacks are supported, got shape {mask.shape}")
    with tifffile.TiffFile(image_path) as tif:
        image_shape = tuple(tif.series[0].shape)
    if tuple(mask.shape) != image_shape:
        raise ValueError(f"image.tif shape {image_shape} does not match mask.tif shape {mask.shape}")

    stats = object_stats(mask)
    nodes = sorted(stats)
    valid_nodes = set(nodes)
    normalized = {}
    tracker_graphs = {}
    for name, path in zip(tracker_names, track_paths):
        rows = normalize_track(path, mask, valid_nodes)
        normalized[name] = rows
        tracker_graphs[name] = successors(rows)

    accepted_edges = {}
    event_rows = []
    source_nodes = [node for node in nodes if node[0] < mask.shape[0] - 1]
    for node in source_nodes:
        signatures = {
            name: signature(node, *tracker_graphs[name]) for name in tracker_names
        }
        distinct = set(signatures.values())
        common = next(iter(distinct)) if len(distinct) == 1 else None
        if common not in (None, "ABSENT", "END"):
            frame, label = (int(value) for value in common.split(":"))
            target = (frame, label)
            if target in valid_nodes:
                accepted_edges[node] = target
        elif len(distinct) > 1:
            area, y, x = stats[node]
            counts = Counter(signatures.values())
            primary_choice = signatures[tracker_names[0]]
            reason = (
                "primary_untracked_object"
                if primary_choice == "ABSENT"
                else "connection_difference"
            )
            event_rows.append(
                {
                    "event_id": f"E{len(event_rows) + 1:05d}",
                    "frame": node[0],
                    "mask_label": node[1],
                    "object_area": area,
                    "object_y": round(y, 3),
                    "object_x": round(x, 3),
                    "reason": reason,
                    "most_common_choice": counts.most_common(1)[0][0],
                    "most_common_votes": counts.most_common(1)[0][1],
                    **{f"{name}_successor": value for name, value in signatures.items()},
                }
            )

    target_counts = Counter(accepted_edges.values())
    conflicting_targets = {target for target, count in target_counts.items() if count > 1}
    if conflicting_targets:
        accepted_edges = {
            source: target for source, target in accepted_edges.items() if target not in conflicting_targets
        }

    track_ids = assign_track_ids(nodes, accepted_edges)
    track_rows = []
    for node in nodes:
        _, y, x = stats[node]
        track_rows.append(
            {
                "frame": node[0],
                "trackId": track_ids[node],
                "state": "none",
                "continuous_label": node[1],
                "Center_of_the_object_0": x,
                "Center_of_the_object_1": y,
            }
        )

    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    link_or_copy(image_path, output_dir / "image.tif")
    link_or_copy(mask_path, output_dir / "mask.tif")
    pd.DataFrame(track_rows, columns=STANDARD_COLUMNS).to_csv(output_dir / "track.csv", index=False)
    event_columns = [
        "event_id",
        "frame",
        "mask_label",
        "object_area",
        "object_y",
        "object_x",
        "reason",
        "most_common_choice",
        "most_common_votes",
        *[f"{name}_successor" for name in tracker_names],
    ]
    pd.DataFrame(event_rows, columns=event_columns).to_csv(
        output_dir / "consensus_events.csv", index=False
    )
    for name, rows in normalized.items():
        rows.to_csv(output_dir / f"normalized_{name}.csv", index=False)
    (output_dir / "config.yaml").write_text(
        "intensity_suffix: image\nmask_suffix: mask\ntrack_suffix: track\n"
        "frame_base: 0\nstateCol: state\n",
        encoding="ascii",
    )

    reason_counts = Counter(row["reason"] for row in event_rows)
    summary = {
        "trackers": tracker_names,
        "tracker_count": len(tracker_names),
        "mask_shape": list(mask.shape),
        "segmented_objects": len(nodes),
        "evaluated_source_objects": len(source_nodes),
        "retained_consensus_links": len(accepted_edges),
        "disagreement_sites": len(event_rows),
        "connection_difference_sites": reason_counts["connection_difference"],
        "primary_untracked_sites": reason_counts["primary_untracked_object"],
        "consensus_track_fragments": len(set(track_ids.values())),
        "rule": "A link is retained only when every imported tracker selects the same successor.",
        "state_value": "none",
    }
    (output_dir / "consensus_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path, default=Path(__file__).resolve().parent / "workspace"
    )
    args = parser.parse_args()
    print(json.dumps(run_consensus(args.workspace), indent=2))


if __name__ == "__main__":
    main()
