from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import tifffile


from .consensus_core import (
    STANDARD_COLUMNS,
    link_or_copy,
    normalize_track,
    object_stats,
    run_consensus,
)


ProgressCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]
STANDARD_FILES = ("image.tif", "mask.tif", "track.csv", "config.yaml")
REFERENCE_TRACKER = "trackpy"
EVENT_COLUMNS = [
    "event_id",
    "frame",
    "mask_label",
    "object_area",
    "object_y",
    "object_x",
    "reason",
    "most_common_choice",
    "most_common_votes",
]


@dataclass(frozen=True)
class CorrectionSelection:
    mode: str
    correction_dir: str
    assistance_dir: str | None = None
    warning: str | None = None


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        try:
            callback(message)
        except Exception as error:
            print(f"Progress callback failed: {error}", flush=True)
    print(message, flush=True)


def _read_tiff_shape(path: Path) -> tuple[int, ...]:
    with tifffile.TiffFile(path) as tif:
        return tuple(int(value) for value in tif.series[0].shape)


def validate_pipeline_result(result_dir: str | Path) -> dict:
    result_dir = Path(result_dir).expanduser().resolve()
    missing = [name for name in STANDARD_FILES if not (result_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("Pipeline result is missing: " + ", ".join(missing))

    image_path = result_dir / "image.tif"
    mask_path = result_dir / "mask.tif"
    image_shape = _read_tiff_shape(image_path)
    mask_shape = _read_tiff_shape(mask_path)
    if len(mask_shape) != 3:
        raise ValueError(f"Only 2D+t mask stacks are supported, got shape {mask_shape}")
    if image_shape != mask_shape:
        raise ValueError(f"image.tif shape {image_shape} does not match mask.tif shape {mask_shape}")
    if mask_shape[0] < 2:
        raise ValueError("Consensus assistance requires at least two frames")

    mask = tifffile.memmap(mask_path)
    if not np.issubdtype(mask.dtype, np.integer):
        raise ValueError(f"mask.tif must use integer labels, got {mask.dtype}")
    if np.issubdtype(mask.dtype, np.signedinteger) and int(np.min(mask)) < 0:
        raise ValueError("mask.tif contains negative labels")
    stats = object_stats(mask)
    if not stats:
        raise ValueError("mask.tif contains no labelled objects")

    track_path = result_dir / "track.csv"
    validate_track_file(track_path, mask, set(stats))
    return {
        "result_dir": result_dir,
        "image_path": image_path,
        "mask_path": mask_path,
        "track_path": track_path,
        "config_path": result_dir / "config.yaml",
        "mask_shape": mask_shape,
        "mask_objects": len(stats),
    }


def validate_track_file(track_path: str | Path, mask, valid_nodes: set[tuple[int, int]]) -> pd.DataFrame:
    track_path = Path(track_path)
    if not track_path.is_file() or track_path.stat().st_size == 0:
        raise FileNotFoundError(f"Tracking result is missing or empty: {track_path}")
    raw = pd.read_csv(track_path)
    if raw.empty:
        raise ValueError(f"Tracking result has no rows: {track_path}")
    missing_standard = [column for column in STANDARD_COLUMNS if column not in raw.columns]
    if missing_standard:
        raise ValueError(
            f"Tracking result is not correction-compatible; missing: {', '.join(missing_standard)}"
        )
    duplicate_objects = raw.duplicated(["frame", "continuous_label"], keep=False)
    if duplicate_objects.any():
        raise ValueError(
            f"Tracking result contains {int(duplicate_objects.sum())} duplicate "
            f"frame/continuous_label rows: {track_path}"
        )
    normalized = normalize_track(track_path, mask, valid_nodes)
    if normalized.empty:
        raise ValueError(f"Tracking result cannot be mapped to mask objects: {track_path}")
    frames = normalized["frame"].astype(int)
    if int(frames.min()) < 0 or int(frames.max()) >= int(mask.shape[0]):
        raise ValueError(f"Tracking result contains frames outside mask.tif: {track_path}")
    return normalized


def track_coverage(normalized: pd.DataFrame, mask_object_count: int) -> dict:
    mapped = int(len(normalized[["frame", "mask_label"]].drop_duplicates()))
    return {
        "mapped_objects": mapped,
        "mask_objects": int(mask_object_count),
        "coverage_percent": round(100 * mapped / mask_object_count, 2),
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


def _trackpy_registration() -> dict:
    from biotrack_studio.paths import load_registry, resolve_algorithm_script

    info = load_registry().get("tracking", {}).get(REFERENCE_TRACKER)
    if not info:
        raise RuntimeError("TrackPy is not registered in BioTrack Studio")
    script = Path(resolve_algorithm_script(info.get("script", "")))
    if not info.get("env") or not script.is_file():
        raise RuntimeError("TrackPy environment or launcher script is unavailable")
    return {**info, "script": str(script)}


def _unique_run_dir(root: Path) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = root / timestamp
    suffix = 1
    while candidate.exists():
        candidate = root / f"{timestamp}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def run_trackpy_reference(
    image_path: Path,
    mask_path: Path,
    run_dir: Path,
    callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> Path:
    try:
        from .env_manager import get_subprocess_env
    except ImportError:
        from biotrack_studio.env_manager import get_subprocess_env

    info = _trackpy_registration()
    link_or_copy(image_path, run_dir / "image.tif")
    link_or_copy(mask_path, run_dir / "mask.tif")
    log_path = run_dir / "trackpy.log"
    command = [
        str(_find_conda_executable()),
        "run",
        "--no-capture-output",
        "-n",
        info["env"],
        "python",
        info["script"],
        str(run_dir),
    ]
    environment = get_subprocess_env()
    _notify(callback, "Running TrackPy reference...")
    process = None
    started = time.monotonic()
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            log.write("Command:\n" + " ".join(command) + "\n\n")
            log.flush()
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            while process.poll() is None:
                if cancel_check and cancel_check():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise RuntimeError("TrackPy assistance was cancelled by the user")
                elapsed = int(time.monotonic() - started)
                if callback:
                    try:
                        callback(f"Running TrackPy reference... {elapsed}s")
                    except Exception as error:
                        print(f"Progress callback failed: {error}", flush=True)
                        callback = None
                time.sleep(0.25)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for temporary in (run_dir / "image.tif", run_dir / "mask.tif"):
            if temporary.exists():
                temporary.unlink()
    if process is None or process.returncode:
        raise RuntimeError(
            f"TrackPy failed with exit code {getattr(process, 'returncode', 'unknown')}. "
            f"See {log_path}"
        )
    track_path = run_dir / "track.csv"
    if not track_path.is_file() or track_path.stat().st_size == 0:
        raise RuntimeError(f"TrackPy did not produce track.csv. See {log_path}")
    _notify(callback, "TrackPy reference finished.")
    return track_path


def _write_failure(root: Path, error: Exception) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "last_failure.json"
    payload = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error_type": type(error).__name__,
        "message": str(error),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def build_assisted_correction(
    result_dir: str | Path,
    primary_name: str,
    *,
    assistance_root: str | Path | None = None,
    reference_track: str | Path | None = None,
    minimum_coverage: float = 0.5,
    callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict:
    if not primary_name or not re.fullmatch(r"[A-Za-z0-9_.-]+", primary_name):
        raise ValueError("Primary tracker name contains unsupported characters")
    if primary_name.lower() == REFERENCE_TRACKER:
        raise ValueError("TrackPy cannot be compared with itself in the first release")
    validated = validate_pipeline_result(result_dir)
    result_dir = validated["result_dir"]
    assistance_root = (
        Path(assistance_root).expanduser().resolve()
        if assistance_root
        else result_dir / "consensus_assist"
    )
    run_dir = _unique_run_dir(assistance_root / "runs")
    workspace = run_dir / "comparison"
    input_dir = workspace / "input"
    input_dir.mkdir(parents=True)
    link_or_copy(validated["image_path"], input_dir / "image.tif")
    link_or_copy(validated["mask_path"], input_dir / "mask.tif")

    primary_dir = workspace / "tracks" / primary_name
    reference_dir = workspace / "tracks" / REFERENCE_TRACKER
    primary_dir.mkdir(parents=True)
    reference_dir.mkdir(parents=True)
    shutil.copy2(validated["track_path"], primary_dir / "track.csv")

    if reference_track:
        reference_source = Path(reference_track).expanduser().resolve()
        if not reference_source.is_file():
            raise FileNotFoundError(f"Reference track does not exist: {reference_source}")
    else:
        reference_run = run_dir / "reference_run"
        reference_run.mkdir()
        reference_source = run_trackpy_reference(
            validated["image_path"],
            validated["mask_path"],
            reference_run,
            callback,
            cancel_check,
        )
    shutil.copy2(reference_source, reference_dir / "track.csv")

    mask = tifffile.memmap(validated["mask_path"])
    valid_nodes = set(object_stats(mask))
    primary_rows = validate_track_file(primary_dir / "track.csv", mask, valid_nodes)
    reference_rows = validate_track_file(reference_dir / "track.csv", mask, valid_nodes)
    coverage = {
        primary_name: track_coverage(primary_rows, validated["mask_objects"]),
        REFERENCE_TRACKER: track_coverage(reference_rows, validated["mask_objects"]),
    }
    for name, report in coverage.items():
        _notify(callback, f"{name} mask coverage: {report['coverage_percent']}%")
        if report["coverage_percent"] < minimum_coverage * 100:
            raise RuntimeError(
                f"{name} covered only {report['mapped_objects']} of "
                f"{report['mask_objects']} mask objects ({report['coverage_percent']}%). "
                "Consensus assistance was disabled to avoid false warnings."
            )

    (workspace / "workspace.json").write_text(
        json.dumps({"trackers": [primary_name, REFERENCE_TRACKER]}, indent=2),
        encoding="utf-8",
    )
    _notify(callback, "Comparing primary tracking with TrackPy...")
    consensus_summary = run_consensus(workspace)
    comparison_output = workspace / "output"
    events_path = comparison_output / "consensus_events.csv"
    events_are_readable = False
    if events_path.is_file() and events_path.stat().st_size:
        try:
            pd.read_csv(events_path, nrows=1)
            events_are_readable = True
        except pd.errors.EmptyDataError:
            pass
    if not events_are_readable:
        pd.DataFrame(
            columns=EVENT_COLUMNS
            + [f"{primary_name}_successor", f"{REFERENCE_TRACKER}_successor"]
        ).to_csv(events_path, index=False)

    correction_dir = run_dir / "correction"
    correction_dir.mkdir()
    link_or_copy(validated["image_path"], correction_dir / "image.tif")
    link_or_copy(validated["mask_path"], correction_dir / "mask.tif")
    shutil.copy2(validated["track_path"], correction_dir / "track.csv")
    shutil.copy2(validated["track_path"], correction_dir / "primary_track_original.csv")
    shutil.copy2(validated["config_path"], correction_dir / "config.yaml")
    shutil.copy2(events_path, correction_dir / "consensus_events.csv")
    shutil.copy2(comparison_output / "track.csv", correction_dir / "consensus_track.csv")
    shutil.copy2(reference_dir / "track.csv", correction_dir / "trackpy_reference.csv")

    evaluated = max(int(consensus_summary["evaluated_source_objects"]), 1)
    disagreement_rate = round(
        100 * int(consensus_summary["disagreement_sites"]) / evaluated, 2
    )
    connection_sites = int(consensus_summary["connection_difference_sites"])
    untracked_sites = int(consensus_summary["primary_untracked_sites"])
    connection_rate = round(100 * connection_sites / evaluated, 2)
    warnings = [
        "Consensus assistance cannot detect segmentation errors shared by both trackers."
    ]
    if connection_rate > 25:
        warnings.append(
            "More than 25% of source objects have different connections; review "
            "tracking and segmentation quality before treating every marker as an error."
        )
    if validated["mask_shape"][0] > 200:
        warnings.append(
            "This is a long sequence; disagreement review may contain many sites."
        )

    summary = {
        "mode": "consensus_assisted_correction",
        "primary_tracker": primary_name,
        "reference_tracker": REFERENCE_TRACKER,
        "source_result_dir": str(result_dir),
        "run_dir": str(run_dir),
        "correction_dir": str(correction_dir),
        "primary_track_preserved": True,
        "correction_target": "A copy of the primary tracker output",
        "coverage": coverage,
        "disagreement_sites": int(consensus_summary["disagreement_sites"]),
        "connection_difference_sites": connection_sites,
        "primary_untracked_sites": untracked_sites,
        "evaluated_source_objects": int(consensus_summary["evaluated_source_objects"]),
        "disagreement_rate_percent": disagreement_rate,
        "connection_difference_rate_percent": connection_rate,
        "warnings": warnings,
    }
    (run_dir / "assist_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    latest = assistance_root / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    temporary = latest.with_suffix(".tmp")
    temporary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    temporary.replace(latest)
    _notify(
        callback,
        "Assisted correction ready: "
        f"{connection_sites} connection differences and "
        f"{untracked_sites} untracked objects.",
    )
    return summary


def select_correction_target(
    result_dir: str | Path,
    primary_name: str,
    enabled: bool,
    **kwargs,
) -> CorrectionSelection:
    result_dir = str(Path(result_dir).expanduser().resolve())
    if not enabled:
        return CorrectionSelection(mode="ordinary", correction_dir=result_dir)
    try:
        summary = build_assisted_correction(result_dir, primary_name, **kwargs)
        return CorrectionSelection(
            mode="consensus_assisted",
            correction_dir=summary["correction_dir"],
            assistance_dir=summary["run_dir"],
        )
    except Exception as error:
        root = Path(kwargs.get("assistance_root") or Path(result_dir) / "consensus_assist")
        failure_path = _write_failure(root, error)
        return CorrectionSelection(
            mode="ordinary_fallback",
            correction_dir=result_dir,
            assistance_dir=str(root),
            warning=f"Consensus assistance failed: {error}. Details: {failure_path}",
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--primary", required=True)
    parser.add_argument("--reference-track", type=Path)
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--assistance-root", type=Path)
    args = parser.parse_args()
    selection = select_correction_target(
        args.result_dir,
        args.primary,
        not args.disabled,
        reference_track=args.reference_track,
        assistance_root=args.assistance_root,
    )
    print(json.dumps(asdict(selection), indent=2))


if __name__ == "__main__":
    main()
