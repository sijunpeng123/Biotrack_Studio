from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd


REQUIRED_EVENT_COLUMNS = {
    "event_id",
    "frame",
    "mask_label",
    "object_y",
    "object_x",
}

UNREVIEWED = "unreviewed"
NO_ISSUE = "no_issue"
PROBLEM = "problem"
CORRECTED = "corrected"
CLASSIFICATIONS = (UNREVIEWED, NO_ISSUE, PROBLEM, CORRECTED)
RESOLVED_CLASSIFICATIONS = {NO_ISSUE, CORRECTED}
CONNECTION_DIFFERENCE = "connection_difference"
PRIMARY_UNTRACKED = "primary_untracked_object"


@dataclass(frozen=True)
class ReviewData:
    events: pd.DataFrame
    tracker_names: list[str]


def tracking_reasons(events: pd.DataFrame) -> pd.Series:
    reasons = events.get(
        "reason", pd.Series("", index=events.index, dtype=str)
    ).fillna("").astype(str)
    legacy = reasons.isin(("", "tracker_disagreement"))
    successor_columns = [
        column for column in events.columns if column.endswith("_successor")
    ]
    if successor_columns:
        primary_absent = (
            events[successor_columns[0]].fillna("").astype(str) == "ABSENT"
        )
        reasons.loc[legacy & primary_absent] = PRIMARY_UNTRACKED
        reasons.loc[legacy & ~primary_absent] = CONNECTION_DIFFERENCE
    else:
        reasons.loc[legacy] = CONNECTION_DIFFERENCE
    return reasons


def _load_event_file(
    events_path: Path,
    event_type: str,
) -> pd.DataFrame | None:
    if not events_path.is_file():
        return None

    try:
        events = pd.read_csv(events_path, dtype={"event_id": str})
    except pd.errors.EmptyDataError:
        events = pd.DataFrame(columns=sorted(REQUIRED_EVENT_COLUMNS))
    missing = sorted(REQUIRED_EVENT_COLUMNS.difference(events.columns))
    if missing:
        raise ValueError(
            f"{events_path.name} is missing required columns: "
            + ", ".join(missing)
        )

    for column in ("frame", "mask_label", "object_y", "object_x"):
        events[column] = pd.to_numeric(events[column], errors="coerce")
    events = events.dropna(
        subset=["event_id", "frame", "mask_label", "object_y", "object_x"]
    ).copy()
    events["frame"] = events["frame"].astype(int)
    events["mask_label"] = events["mask_label"].astype(int)
    events["event_type"] = event_type
    if event_type == "tracking":
        events["reason"] = tracking_reasons(events)
    if "severity" not in events.columns:
        events["severity"] = "review"
    if event_type == "tracking":
        events.loc[events["reason"] == CONNECTION_DIFFERENCE, "severity"] = "high"
        events.loc[events["reason"] == PRIMARY_UNTRACKED, "severity"] = "review"
    return events


def load_review_data(output_dir: str | Path) -> ReviewData | None:
    output_dir = Path(output_dir)
    tracking_events = _load_event_file(
        output_dir / "consensus_events.csv", "tracking"
    )
    mask_events = _load_event_file(
        output_dir / "mask_consensus_events.csv", "mask"
    )
    available = [
        events
        for events in (tracking_events, mask_events)
        if events is not None
    ]
    if not available:
        return None
    events = pd.concat(available, ignore_index=True, sort=False)
    events = events.sort_values(
        ["event_type", "frame", "mask_label", "event_id"],
        kind="mergesort",
    ).reset_index(drop=True)

    tracker_names = [
        column[: -len("_successor")]
        for column in events.columns
        if column.endswith("_successor")
    ]
    return ReviewData(events=events, tracker_names=tracker_names)


class ReviewStatusStore:
    COLUMNS = ("event_id", "status", "classification", "updated_at")

    def __init__(self, output_dir: str | Path):
        self.path = Path(output_dir) / "consensus_review_status.csv"

    def load(self) -> pd.DataFrame:
        if not self.path.is_file():
            return pd.DataFrame(columns=self.COLUMNS)
        try:
            data = pd.read_csv(
                self.path, dtype={"event_id": str, "status": str}
            )
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=self.COLUMNS)
        for column in self.COLUMNS:
            if column not in data.columns:
                data[column] = ""
        legacy_resolved = (
            data["classification"].fillna("").astype(str).str.strip().eq("")
            & data["status"].fillna("").astype(str).eq("resolved")
        )
        data.loc[legacy_resolved, "classification"] = NO_ISSUE
        data["classification"] = data["classification"].fillna(UNREVIEWED)
        invalid = ~data["classification"].isin(CLASSIFICATIONS)
        data.loc[invalid, "classification"] = UNREVIEWED
        return data.loc[:, self.COLUMNS].drop_duplicates("event_id", keep="last")

    def classifications(self) -> dict[str, str]:
        data = self.load()
        return dict(zip(data["event_id"].astype(str), data["classification"]))

    def classification_for(self, event_id: str) -> str:
        return self.classifications().get(str(event_id), UNREVIEWED)

    def resolved_ids(self) -> set[str]:
        data = self.load()
        resolved = data["classification"].isin(RESOLVED_CLASSIFICATIONS)
        return set(data.loc[resolved, "event_id"].astype(str))

    def set_classification(self, event_id: str, classification: str) -> None:
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"Unknown review classification: {classification}")
        data = self.load()
        data = data[data["event_id"].astype(str) != str(event_id)].copy()
        row = pd.DataFrame(
            [
                {
                    "event_id": str(event_id),
                    "status": (
                        "resolved"
                        if classification in RESOLVED_CLASSIFICATIONS
                        else "open"
                    ),
                    "classification": classification,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        )
        data = pd.concat([data, row], ignore_index=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        data.loc[:, self.COLUMNS].to_csv(temporary, index=False)
        temporary.replace(self.path)

    def set_resolved(self, event_id: str, resolved: bool) -> None:
        self.set_classification(event_id, NO_ISSUE if resolved else UNREVIEWED)

    def review_table(self, events: pd.DataFrame) -> pd.DataFrame:
        table = events.copy()
        status = self.load()
        table["event_id"] = table["event_id"].astype(str)
        table = table.merge(status, on="event_id", how="left")
        table["classification"] = table["classification"].fillna(UNREVIEWED)
        table["status"] = table["status"].fillna("open")
        table["updated_at"] = table["updated_at"].fillna("")
        table["reviewed"] = table["classification"] != UNREVIEWED
        table["needs_attention"] = table["classification"].isin(
            (UNREVIEWED, PROBLEM)
        )
        return table

    def statistics(self, events: pd.DataFrame) -> dict[str, int | float]:
        table = self.review_table(events)
        total = int(len(table))
        counts = table["classification"].value_counts()
        event_types = table.get(
            "event_type", pd.Series("", index=table.index, dtype=str)
        )
        reasons = tracking_reasons(table)
        severities = table.get(
            "severity", pd.Series("", index=table.index, dtype=str)
        ).astype(str).str.lower()
        to_review = int(counts.get(UNREVIEWED, 0))
        problem = int(counts.get(PROBLEM, 0))
        corrected = int(counts.get(CORRECTED, 0))
        no_issue = int(counts.get(NO_ISSUE, 0))
        reviewed = total - to_review
        return {
            "initial_flagged": total,
            "to_review": to_review,
            "reviewed": reviewed,
            "problem_found": problem,
            "corrected": corrected,
            "no_issue": no_issue,
            "needs_attention": to_review + problem,
            "connection_differences": int(
                ((event_types == "tracking") & (reasons == CONNECTION_DIFFERENCE)).sum()
            ),
            "primary_untracked_objects": int(
                ((event_types == "tracking") & (reasons == PRIMARY_UNTRACKED)).sum()
            ),
            "high_priority_mask_sites": int(
                ((event_types == "mask") & (severities == "high")).sum()
            ),
            "review_priority_mask_sites": int(
                ((event_types == "mask") & (severities != "high")).sum()
            ),
            "review_progress_percent": (
                round(100.0 * reviewed / total, 1) if total else 100.0
            ),
            "problem_correction_percent": (
                round(100.0 * corrected / (problem + corrected), 1)
                if problem + corrected
                else 0.0
            ),
        }

    def export_reports(
        self,
        events: pd.DataFrame,
        tracker_names: list[str],
    ) -> tuple[Path, Path]:
        report = self.review_table(events)
        mask_models = _load_mask_model_names(self.path.parent)
        report["comparison"] = report["event_type"].map(
            {
                "tracking": " vs ".join(tracker_names) or "tracking tools",
                "mask": " vs ".join(mask_models) or "segmentation masks",
            }
        )
        leading = [
            "event_id",
            "event_type",
            "comparison",
            "frame",
            "mask_label",
            "classification",
            "status",
            "reviewed",
            "needs_attention",
            "updated_at",
        ]
        trailing = [column for column in report.columns if column not in leading]
        report = report.loc[:, leading + trailing]

        generated_at = datetime.now(timezone.utc).isoformat()
        rows = []
        scopes = [("all", events)]
        scopes.extend(
            (mode, events[events["event_type"] == mode])
            for mode in ("tracking", "mask")
            if bool((events["event_type"] == mode).any())
        )
        comparison_names = {
            "all": "All consensus tools",
            "tracking": " vs ".join(tracker_names) or "tracking tools",
            "mask": " vs ".join(mask_models) or "segmentation masks",
        }
        for scope, scoped_events in scopes:
            rows.append(
                {
                    "scope": scope,
                    "comparison": comparison_names[scope],
                    **self.statistics(scoped_events),
                    "generated_at": generated_at,
                }
            )

        report_path = self.path.parent / "consensus_review_report.csv"
        summary_path = self.path.parent / "consensus_review_summary.csv"
        _write_csv_atomic(report, report_path)
        _write_csv_atomic(pd.DataFrame(rows), summary_path)
        return report_path, summary_path


def _load_mask_model_names(output_dir: Path) -> list[str]:
    settings_path = output_dir / "mask_consensus_settings.json"
    if not settings_path.is_file():
        return []
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [
        str(payload[key]).strip()
        for key in ("primary_model", "reference_model")
        if str(payload.get(key, "")).strip()
    ]


def _write_csv_atomic(data: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    data.to_csv(temporary, index=False)
    temporary.replace(path)


def describe_choices(event: pd.Series, tracker_names: list[str]) -> str:
    lines = []
    for name in tracker_names:
        value = str(event.get(f"{name}_successor", "")).strip()
        lines.append(f"{name}: {value or 'not available'}")
    return "\n".join(lines) if lines else "No tracker choices were recorded."


def describe_mask_comparison(event: pd.Series) -> str:
    reference = str(event.get("reference_model", "")).strip() or "reference"
    agreement = pd.to_numeric(event.get("agreement_percent"), errors="coerce")
    centroid = pd.to_numeric(event.get("centroid_ratio"), errors="coerce")
    area_ratio = pd.to_numeric(event.get("area_ratio"), errors="coerce")
    lines = [f"Reference: {reference}"]
    if pd.notna(agreement):
        lines.append(f"Mask agreement: {float(agreement):.1f}%")
    if pd.notna(centroid):
        lines.append(f"Centroid distance: {float(centroid):.2f} diameters")
    if pd.notna(area_ratio):
        lines.append(f"Area ratio: {float(area_ratio):.2f}")
    return "\n".join(lines)
