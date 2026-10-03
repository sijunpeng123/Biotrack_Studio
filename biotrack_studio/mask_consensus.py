"""Prepare optional segmentation-disagreement assistance for manual correction."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import tifffile

ProgressCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]
AUTOMATIC_REFERENCE = "Automatic"
DEFAULT_REFERENCE = "stardist"
STARDIST_FALLBACK_REFERENCE = "cellpose_cyto"
RUN_METADATA_NAME = "BioTrack_Studio_run.json"
MASK_EVENT_COLUMNS = [
    "event_id",
    "event_type",
    "frame",
    "mask_label",
    "reference_label",
    "object_area",
    "object_y",
    "object_x",
    "reason",
    "severity",
    "agreement_percent",
    "iou",
    "centroid_ratio",
    "area_ratio",
    "reference_model",
]


class IncompatibleMaskModelsError(RuntimeError):
    pass


def link_or_copy(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    if destination.exists():
        destination.unlink()
    shutil.copy2(source, destination)


def as_time_stack(data: np.ndarray) -> np.ndarray:
    if data.ndim == 2:
        return data[np.newaxis, ...]
    if data.ndim != 3:
        raise ValueError(
            f"Expected a 2D image or 3D time stack, received shape {data.shape}"
        )
    return data


@dataclass(frozen=True)
class MaskConsensusSettings:
    centroid_ratio: float = 0.50
    review_iou: float = 0.70
    priority_iou: float = 0.50
    small_object_ratio: float = 0.25
    compatibility_coverage: float = 0.70
    minimum_count_ratio: float = 0.50
    split_overlap_fraction: float = 0.20

    def validate(self) -> "MaskConsensusSettings":
        if not 0 < self.centroid_ratio <= 2:
            raise ValueError("Centroid ratio must be greater than 0 and at most 2")
        if not 0 <= self.priority_iou < self.review_iou <= 1:
            raise ValueError(
                "High-priority IoU must be lower than Low-priority IoU, within 0 to 1"
            )
        if not 0 <= self.small_object_ratio <= 1:
            raise ValueError("Small-object ratio must be between 0 and 1")
        if not 0 <= self.compatibility_coverage <= 1:
            raise ValueError("Compatibility coverage must be between 0 and 1")
        if not 0 <= self.minimum_count_ratio <= 1:
            raise ValueError("Minimum count ratio must be between 0 and 1")
        if not 0 < self.split_overlap_fraction <= 1:
            raise ValueError("Split/merge overlap fraction must be within 0 to 1")
        return self


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        callback(message)
    print(message, flush=True)


def _unique_run_dir(root: Path) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = root / timestamp
    suffix = 1
    while candidate.exists():
        candidate = root / f"{timestamp}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def detect_primary_segmentation(result_dir: str | Path) -> str | None:
    path = Path(result_dir) / RUN_METADATA_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get(
            "segmentation_method"
        )
    except Exception:
        return None
    if not isinstance(value, str) or not value or value.lower().startswith("skip"):
        return None
    return value


def choose_reference_model(
    result_dir: str | Path,
    requested: str = AUTOMATIC_REFERENCE,
) -> tuple[str, str | None]:
    primary = detect_primary_segmentation(result_dir)
    if requested and requested != AUTOMATIC_REFERENCE:
        reference = requested
    elif not primary:
        raise ValueError(
            "The primary segmentation method is unknown. Select the Mask "
            "Reference manually instead of Automatic."
        )
    elif primary and primary.lower() == DEFAULT_REFERENCE:
        reference = STARDIST_FALLBACK_REFERENCE
    else:
        reference = DEFAULT_REFERENCE
    if primary and reference.lower() == primary.lower():
        raise ValueError(
            f"{reference} cannot be compared with itself. Select another reference model."
        )
    return reference, primary


def _plane_stats(plane: np.ndarray) -> dict[int, dict[str, float]]:
    ys, xs = np.nonzero(plane)
    if not len(ys):
        return {}
    labels = plane[ys, xs].astype(np.int64, copy=False)
    counts = np.bincount(labels)
    sum_y = np.bincount(labels, weights=ys)
    sum_x = np.bincount(labels, weights=xs)
    stats = {}
    for label in np.flatnonzero(counts):
        if label == 0:
            continue
        area = int(counts[label])
        stats[int(label)] = {
            "area": area,
            "y": float(sum_y[label] / area),
            "x": float(sum_x[label] / area),
            "diameter": float(math.sqrt(4.0 * area / math.pi)),
        }
    return stats


def _active_labels(
    stats: dict[int, dict[str, float]],
    small_object_ratio: float,
) -> tuple[list[int], int]:
    if not stats:
        return [], 0
    median_area = float(np.median([item["area"] for item in stats.values()]))
    minimum_area = small_object_ratio * median_area
    active = [
        label
        for label, item in stats.items()
        if float(item["area"]) >= minimum_area
    ]
    return sorted(active), len(stats) - len(active)


def _intersections(
    primary: np.ndarray,
    reference: np.ndarray,
) -> dict[tuple[int, int], int]:
    both = (primary > 0) & (reference > 0)
    if not np.any(both):
        return {}
    base = int(reference.max()) + 1
    codes = (
        primary[both].astype(np.int64, copy=False) * base
        + reference[both].astype(np.int64, copy=False)
    )
    unique, counts = np.unique(codes, return_counts=True)
    return {
        (int(code // base), int(code % base)): int(count)
        for code, count in zip(unique, counts)
    }


def _candidate_records(
    primary_stats: dict[int, dict[str, float]],
    reference_stats: dict[int, dict[str, float]],
    primary_labels: list[int],
    reference_labels: list[int],
    intersections: dict[tuple[int, int], int],
    settings: MaskConsensusSettings,
) -> list[dict]:
    candidates = []
    for primary_label in primary_labels:
        primary_item = primary_stats[primary_label]
        for reference_label in reference_labels:
            reference_item = reference_stats[reference_label]
            distance = float(
                np.hypot(
                    primary_item["y"] - reference_item["y"],
                    primary_item["x"] - reference_item["x"],
                )
            )
            mean_diameter = (
                primary_item["diameter"] + reference_item["diameter"]
            ) / 2.0
            centroid_ratio = (
                distance / mean_diameter if mean_diameter > 0 else float("inf")
            )
            intersection = intersections.get(
                (primary_label, reference_label), 0
            )
            if centroid_ratio > settings.centroid_ratio and intersection == 0:
                continue
            union = (
                primary_item["area"] + reference_item["area"] - intersection
            )
            iou = intersection / union if union > 0 else 0.0
            area_ratio = min(
                primary_item["area"], reference_item["area"]
            ) / max(primary_item["area"], reference_item["area"])
            candidates.append(
                {
                    "primary_label": primary_label,
                    "reference_label": reference_label,
                    "intersection": intersection,
                    "iou": float(iou),
                    "centroid_ratio": float(centroid_ratio),
                    "area_ratio": float(area_ratio),
                }
            )
    return candidates


def _greedy_pairs(candidates: list[dict]) -> list[dict]:
    ordered = sorted(
        candidates,
        key=lambda row: (
            -row["iou"],
            row["centroid_ratio"],
            -row["area_ratio"],
            row["primary_label"],
            row["reference_label"],
        ),
    )
    paired_primary = set()
    paired_reference = set()
    pairs = []
    for candidate in ordered:
        primary_label = candidate["primary_label"]
        reference_label = candidate["reference_label"]
        if (
            primary_label in paired_primary
            or reference_label in paired_reference
        ):
            continue
        paired_primary.add(primary_label)
        paired_reference.add(reference_label)
        pairs.append(candidate)
    return pairs


def match_mask_objects(
    primary: np.ndarray,
    reference: np.ndarray,
    settings: MaskConsensusSettings,
) -> dict:
    settings.validate()
    primary_stats = _plane_stats(primary)
    reference_stats = _plane_stats(reference)
    primary_labels, ignored_primary = _active_labels(
        primary_stats, settings.small_object_ratio
    )
    reference_labels, ignored_reference = _active_labels(
        reference_stats, settings.small_object_ratio
    )
    intersections = _intersections(primary, reference)
    candidates = _candidate_records(
        primary_stats,
        reference_stats,
        primary_labels,
        reference_labels,
        intersections,
        settings,
    )
    pairs = _greedy_pairs(candidates)
    paired_primary = {row["primary_label"] for row in pairs}
    paired_reference = {row["reference_label"] for row in pairs}
    return {
        "primary_stats": primary_stats,
        "reference_stats": reference_stats,
        "primary_labels": primary_labels,
        "reference_labels": reference_labels,
        "ignored_primary": ignored_primary,
        "ignored_reference": ignored_reference,
        "intersections": intersections,
        "pairs": pairs,
        "unmatched_primary": sorted(set(primary_labels) - paired_primary),
        "unmatched_reference": sorted(set(reference_labels) - paired_reference),
    }


def compatibility_report(
    primary_masks: np.ndarray,
    reference_masks: np.ndarray,
    settings: MaskConsensusSettings,
) -> dict:
    if primary_masks.shape != reference_masks.shape:
        raise ValueError(
            f"Mask shapes do not match: {primary_masks.shape} and "
            f"{reference_masks.shape}"
        )
    primary_count = 0
    reference_count = 0
    matches = 0
    ignored_primary = 0
    ignored_reference = 0
    for primary, reference in zip(primary_masks, reference_masks):
        result = match_mask_objects(primary, reference, settings)
        primary_count += len(result["primary_labels"])
        reference_count += len(result["reference_labels"])
        matches += len(result["pairs"])
        ignored_primary += result["ignored_primary"]
        ignored_reference += result["ignored_reference"]
    count_ratio = min(primary_count, reference_count) / max(
        primary_count, reference_count, 1
    )
    coverage = 2 * matches / max(primary_count + reference_count, 1)
    compatible = (
        count_ratio >= settings.minimum_count_ratio
        and coverage >= settings.compatibility_coverage
    )
    return {
        "compatible": bool(compatible),
        "primary_objects": primary_count,
        "reference_objects": reference_count,
        "matched_pairs": matches,
        "count_ratio": round(float(count_ratio), 4),
        "match_coverage": round(float(coverage), 4),
        "ignored_small_primary": ignored_primary,
        "ignored_small_reference": ignored_reference,
        "required_count_ratio": settings.minimum_count_ratio,
        "required_match_coverage": settings.compatibility_coverage,
    }


def _event_row(
    *,
    frame: int,
    primary_label: int,
    reference_label: int,
    item: dict[str, float],
    reason: str,
    severity: str,
    reference_model: str,
    iou: float = 0.0,
    centroid_ratio: float = float("nan"),
    area_ratio: float = 0.0,
) -> dict:
    return {
        "event_id": "",
        "event_type": "mask",
        "frame": int(frame),
        "mask_label": int(primary_label),
        "reference_label": int(reference_label),
        "object_area": int(item["area"]),
        "object_y": round(float(item["y"]), 3),
        "object_x": round(float(item["x"]), 3),
        "reason": reason,
        "severity": severity,
        "agreement_percent": round(100 * float(iou), 1),
        "iou": round(float(iou), 6),
        "centroid_ratio": (
            round(float(centroid_ratio), 6)
            if np.isfinite(centroid_ratio)
            else np.nan
        ),
        "area_ratio": round(float(area_ratio), 6),
        "reference_model": reference_model,
    }


def analyze_mask_frame(
    primary: np.ndarray,
    reference: np.ndarray,
    settings: MaskConsensusSettings,
    frame: int,
    reference_model: str,
) -> list[dict]:
    result = match_mask_objects(primary, reference, settings)
    events = []
    topology_primary = set()
    topology_reference = set()
    for primary_label in result["primary_labels"]:
        related = [
            reference_label
            for reference_label in result["reference_labels"]
            if result["intersections"].get(
                (primary_label, reference_label), 0
            )
            / min(
                result["primary_stats"][primary_label]["area"],
                result["reference_stats"][reference_label]["area"],
            )
            >= settings.split_overlap_fraction
        ]
        if len(related) > 1:
            topology_primary.add(primary_label)
            topology_reference.update(related)
    for reference_label in result["reference_labels"]:
        related = [
            primary_label
            for primary_label in result["primary_labels"]
            if result["intersections"].get(
                (primary_label, reference_label), 0
            )
            / min(
                result["primary_stats"][primary_label]["area"],
                result["reference_stats"][reference_label]["area"],
            )
            >= settings.split_overlap_fraction
        ]
        if len(related) > 1:
            topology_reference.add(reference_label)
            topology_primary.update(related)

    for pair in result["pairs"]:
        primary_label = pair["primary_label"]
        reference_label = pair["reference_label"]
        if (
            primary_label in topology_primary
            or reference_label in topology_reference
        ):
            events.append(
                _event_row(
                    frame=frame,
                    primary_label=primary_label,
                    reference_label=reference_label,
                    item=result["primary_stats"][primary_label],
                    reason="possible_split_or_merge",
                    severity="high",
                    reference_model=reference_model,
                    iou=pair["iou"],
                    centroid_ratio=pair["centroid_ratio"],
                    area_ratio=pair["area_ratio"],
                )
            )
        elif pair["iou"] < settings.priority_iou:
            events.append(
                _event_row(
                    frame=frame,
                    primary_label=primary_label,
                    reference_label=reference_label,
                    item=result["primary_stats"][primary_label],
                    reason="low_mask_agreement",
                    severity="high",
                    reference_model=reference_model,
                    iou=pair["iou"],
                    centroid_ratio=pair["centroid_ratio"],
                    area_ratio=pair["area_ratio"],
                )
            )
        elif pair["iou"] < settings.review_iou:
            events.append(
                _event_row(
                    frame=frame,
                    primary_label=primary_label,
                    reference_label=reference_label,
                    item=result["primary_stats"][primary_label],
                    reason="moderate_mask_agreement",
                    severity="review",
                    reference_model=reference_model,
                    iou=pair["iou"],
                    centroid_ratio=pair["centroid_ratio"],
                    area_ratio=pair["area_ratio"],
                )
            )

    paired_primary = {row["primary_label"] for row in result["pairs"]}
    paired_reference = {row["reference_label"] for row in result["pairs"]}
    for primary_label in result["unmatched_primary"]:
        if primary_label in topology_primary or primary_label in paired_primary:
            continue
        events.append(
            _event_row(
                frame=frame,
                primary_label=primary_label,
                reference_label=0,
                item=result["primary_stats"][primary_label],
                reason="primary_only_object",
                severity="high",
                reference_model=reference_model,
            )
        )
    for reference_label in result["unmatched_reference"]:
        if (
            reference_label in topology_reference
            or reference_label in paired_reference
        ):
            continue
        events.append(
            _event_row(
                frame=frame,
                primary_label=0,
                reference_label=reference_label,
                item=result["reference_stats"][reference_label],
                reason="reference_only_object",
                severity="high",
                reference_model=reference_model,
            )
        )
    return sorted(
        events,
        key=lambda row: (
            0 if row["severity"] == "high" else 1,
            row["object_y"],
            row["object_x"],
            row["reason"],
        ),
    )


def analyze_mask_stacks(
    primary_masks: np.ndarray,
    reference_masks: np.ndarray,
    settings: MaskConsensusSettings,
    reference_model: str,
) -> tuple[pd.DataFrame, dict]:
    if primary_masks.shape != reference_masks.shape:
        raise ValueError(
            f"Mask shapes do not match: {primary_masks.shape} and "
            f"{reference_masks.shape}"
        )
    rows = []
    for frame, (primary, reference) in enumerate(
        zip(primary_masks, reference_masks)
    ):
        rows.extend(
            analyze_mask_frame(
                primary, reference, settings, frame, reference_model
            )
        )
    for index, row in enumerate(rows, start=1):
        row["event_id"] = f"M{index:05d}"
    events = pd.DataFrame(rows, columns=MASK_EVENT_COLUMNS)
    high = (
        int((events["severity"] == "high").sum()) if len(events) else 0
    )
    review = (
        int((events["severity"] == "review").sum()) if len(events) else 0
    )
    return events, {
        "mask_disagreement_sites": int(len(events)),
        "high_priority_sites": high,
        "review_sites": review,
    }


def _find_conda_executable() -> Path:
    candidates = []
    for name in ("CONDA_EXE", "MAMBA_EXE"):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]))
    home = Path.home()
    candidates.extend(
        [
            home / "miniconda3" / "Scripts" / "conda.exe",
            home / "anaconda3" / "Scripts" / "conda.exe",
            home / "miniforge3" / "Scripts" / "conda.exe",
            home / "miniconda3" / "bin" / "conda",
            home / "miniforge3" / "bin" / "conda",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    located = shutil.which("conda") or shutil.which("mamba")
    if located:
        return Path(located)
    raise FileNotFoundError("Conda executable was not found")


def _segmentation_registration(reference_model: str) -> dict:
    from .paths import load_registry, resolve_algorithm_script

    info = load_registry().get("segmentation", {}).get(reference_model)
    if not info:
        raise RuntimeError(
            f"Segmentation reference is not registered: {reference_model}"
        )
    script = Path(resolve_algorithm_script(info.get("script", "")))
    if not info.get("env") or not script.is_file():
        raise RuntimeError(
            f"Segmentation environment or launcher is unavailable: {reference_model}"
        )
    return {**info, "script": str(script)}


def run_segmentation_reference(
    reference_model: str,
    image_path: Path,
    output_dir: Path,
    callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> Path:
    from .env_manager import get_subprocess_env

    info = _segmentation_registration(reference_model)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"{reference_model}.log"
    command = [
        str(_find_conda_executable()),
        "run",
        "--no-capture-output",
        "-n",
        info["env"],
        "python",
        info["script"],
        str(image_path),
        str(output_dir),
    ]
    _notify(callback, f"Running {reference_model} mask reference...")
    process = None
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write("Command:\n" + " ".join(command) + "\n\n")
        log.flush()
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=get_subprocess_env(),
        )
        while process.poll() is None:
            if cancel_check and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise RuntimeError("Mask consensus was cancelled by the user")
            elapsed = int(time.monotonic() - started)
            if callback:
                callback(
                    f"Running {reference_model} mask reference... {elapsed}s"
                )
            time.sleep(0.25)
    if process.returncode:
        raise RuntimeError(
            f"{reference_model} failed with exit code {process.returncode}. "
            f"See {log_path}"
        )
    mask_path = output_dir / "mask.tif"
    if not mask_path.is_file() or mask_path.stat().st_size == 0:
        raise RuntimeError(
            f"{reference_model} did not produce mask.tif. See {log_path}"
        )
    return mask_path


def _sample_indices(frame_count: int, count: int = 5) -> np.ndarray:
    if frame_count <= count:
        return np.arange(frame_count, dtype=int)
    return np.unique(
        np.rint(np.linspace(0, frame_count - 1, count)).astype(int)
    )


def _copy_standard_workspace(
    result_dir: Path,
    correction_dir: Path,
) -> None:
    correction_dir.mkdir(parents=True)
    for name in (
        "image.tif",
        "mask.tif",
        "track.csv",
        "config.yaml",
        RUN_METADATA_NAME,
    ):
        source = result_dir / name
        if source.is_file():
            link_or_copy(source, correction_dir / name)


def build_mask_assisted_correction(
    result_dir: str | Path,
    *,
    existing_correction_dir: str | Path | None = None,
    assistance_root: str | Path | None = None,
    reference_model: str = AUTOMATIC_REFERENCE,
    reference_mask: str | Path | None = None,
    settings: MaskConsensusSettings | None = None,
    callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict:
    from .consensus_assist import validate_pipeline_result

    settings = (settings or MaskConsensusSettings()).validate()
    validated = validate_pipeline_result(result_dir)
    result_dir = validated["result_dir"]
    selected_reference, primary_model = choose_reference_model(
        result_dir, reference_model
    )
    root = (
        Path(assistance_root).expanduser().resolve()
        if assistance_root
        else result_dir / "consensus_assist"
    )
    run_dir = _unique_run_dir(root / "mask_runs")
    primary_masks = as_time_stack(tifffile.memmap(validated["mask_path"]))
    indices = _sample_indices(int(primary_masks.shape[0]))

    if reference_mask:
        reference_path = Path(reference_mask).expanduser().resolve()
        if not reference_path.is_file():
            raise FileNotFoundError(
                f"Reference mask does not exist: {reference_path}"
            )
    else:
        image = as_time_stack(tifffile.memmap(validated["image_path"]))
        precheck_input = run_dir / "precheck_image.tif"
        tifffile.imwrite(precheck_input, np.asarray(image[indices]))
        precheck_dir = run_dir / "precheck_reference"
        precheck_path = run_segmentation_reference(
            selected_reference,
            precheck_input,
            precheck_dir,
            callback,
            cancel_check,
        )
        precheck_masks = as_time_stack(tifffile.memmap(precheck_path))
        report = compatibility_report(
            np.asarray(primary_masks[indices]),
            precheck_masks,
            settings,
        )
        (run_dir / "precheck_compatibility.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        if not report["compatible"]:
            raise IncompatibleMaskModelsError(
                f"{selected_reference} is incompatible with the current mask "
                f"(count ratio {report['count_ratio']:.1%}, match coverage "
                f"{report['match_coverage']:.1%})."
            )
        if len(indices) == int(primary_masks.shape[0]):
            reference_path = precheck_path
        else:
            full_dir = run_dir / "reference_run"
            reference_path = run_segmentation_reference(
                selected_reference,
                validated["image_path"],
                full_dir,
                callback,
                cancel_check,
            )

    reference_masks = as_time_stack(tifffile.memmap(reference_path))
    if tuple(reference_masks.shape) != tuple(primary_masks.shape):
        raise ValueError(
            f"Reference mask shape {reference_masks.shape} does not match "
            f"primary mask shape {primary_masks.shape}"
        )
    compatibility = compatibility_report(
        np.asarray(primary_masks[indices]),
        np.asarray(reference_masks[indices]),
        settings,
    )
    if not compatibility["compatible"]:
        raise IncompatibleMaskModelsError(
            f"{selected_reference} is incompatible with the current mask "
            f"(count ratio {compatibility['count_ratio']:.1%}, match coverage "
            f"{compatibility['match_coverage']:.1%})."
        )

    _notify(callback, "Comparing primary and reference masks...")
    events, counts = analyze_mask_stacks(
        primary_masks,
        reference_masks,
        settings,
        selected_reference,
    )

    if existing_correction_dir:
        correction_dir = Path(existing_correction_dir).expanduser().resolve()
        if not correction_dir.is_dir():
            raise FileNotFoundError(
                f"Correction workspace does not exist: {correction_dir}"
            )
    else:
        correction_dir = run_dir / "correction"
        _copy_standard_workspace(result_dir, correction_dir)

    events.to_csv(correction_dir / "mask_consensus_events.csv", index=False)
    link_or_copy(reference_path, correction_dir / "mask_reference.tif")
    settings_payload = {
        **asdict(settings),
        "reference_model": selected_reference,
        "primary_model": primary_model,
    }
    (correction_dir / "mask_consensus_settings.json").write_text(
        json.dumps(settings_payload, indent=2), encoding="utf-8"
    )
    summary = {
        "mode": "mask_consensus_assisted_correction",
        "source_result_dir": str(result_dir),
        "run_dir": str(run_dir),
        "correction_dir": str(correction_dir),
        "primary_model": primary_model,
        "reference_model": selected_reference,
        "primary_mask_preserved": True,
        "compatibility": compatibility,
        **counts,
        "settings": asdict(settings),
        "warning": (
            "Disagreement markers are review priorities, not confirmed errors."
        ),
    }
    (run_dir / "mask_assist_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (correction_dir / "mask_consensus_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    _notify(
        callback,
        "Mask assistance ready: "
        f"{counts['high_priority_sites']} high-priority and "
        f"{counts['review_sites']} low-priority sites.",
    )
    return summary
