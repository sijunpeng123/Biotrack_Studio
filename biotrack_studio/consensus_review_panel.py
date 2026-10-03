from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import tifffile
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

try:
    from .consensus_review_model import (
        CORRECTED,
        CONNECTION_DIFFERENCE,
        NO_ISSUE,
        PRIMARY_UNTRACKED,
        PROBLEM,
        UNREVIEWED,
        ReviewStatusStore,
        describe_choices,
        describe_mask_comparison,
        load_review_data,
    )
    from .mask_consensus import (
        MaskConsensusSettings,
        analyze_mask_frame,
        as_time_stack,
    )
except ImportError:
    from consensus_review_model import (
        CORRECTED,
        CONNECTION_DIFFERENCE,
        NO_ISSUE,
        PRIMARY_UNTRACKED,
        PROBLEM,
        UNREVIEWED,
        ReviewStatusStore,
        describe_choices,
        describe_mask_comparison,
        load_review_data,
    )
    from mask_consensus import (
        MaskConsensusSettings,
        analyze_mask_frame,
        as_time_stack,
    )


HIGH_COLOR = "#e45756"
REVIEW_COLOR = "#f2c14e"
CURRENT_COLOR = "#00d6c9"
ALL_SITES = "All sites"
CONNECTION_SITES = "Connection differences"
UNTRACKED_SITES = "Untracked objects"
HIGH_PRIORITY_SITES = "High priority"
LOW_PRIORITY_SITES = "Low priority"
NEEDS_ATTENTION = "Needs attention"
UNREVIEWED_STATUS = "Unreviewed"
PROBLEM_FOUND = "Problem found"
CORRECTED_STATUS = "Corrected"
NO_ISSUE_STATUS = "No issue"
ALL_STATUSES = "All statuses"


class ConsensusReviewPanel(QWidget):
    def __init__(self, viewer, output_dir: str | Path, correction_name: str):
        super().__init__()
        self.viewer = viewer
        self.output_dir = Path(output_dir)
        review_data = load_review_data(self.output_dir)
        if review_data is None:
            raise FileNotFoundError("Consensus event files were not found")
        self.all_events = review_data.events
        self.tracker_names = review_data.tracker_names
        self.status_store = ReviewStatusStore(self.output_dir)
        self.mode_names = [
            mode
            for mode in ("tracking", "mask")
            if bool((self.all_events["event_type"] == mode).any())
            or self._event_file_exists(mode)
        ]
        self.current_mode = self.mode_names[0]
        self.mode_events = self._events_for_mode(self.current_mode)
        self.events = self.mode_events.copy()
        self.current_row = 0
        self.reference_mask = None
        self.mask_settings = None
        self.mask_reference_model = "reference"
        self.primary_labels_layer = None

        self._load_mask_review_context()
        self._build_ui(correction_name)
        self._add_marker_layers()
        self._connect_primary_mask_layer()
        self._show_first_filtered()

    def _event_file_exists(self, mode: str) -> bool:
        name = (
            "consensus_events.csv"
            if mode == "tracking"
            else "mask_consensus_events.csv"
        )
        return (self.output_dir / name).is_file()

    def _events_for_mode(self, mode: str):
        return self.all_events[
            self.all_events["event_type"] == mode
        ].reset_index(drop=True)

    def _load_mask_review_context(self) -> None:
        reference_path = self.output_dir / "mask_reference.tif"
        settings_path = self.output_dir / "mask_consensus_settings.json"
        if not reference_path.is_file() or not settings_path.is_file():
            return
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            allowed = MaskConsensusSettings.__dataclass_fields__
            values = {
                key: payload[key]
                for key in allowed
                if key in payload
            }
            self.mask_settings = MaskConsensusSettings(**values).validate()
            self.mask_reference_model = str(
                payload.get("reference_model") or "reference"
            )
            self.reference_mask = as_time_stack(tifffile.memmap(reference_path))
        except Exception as error:
            print(f"Cannot enable mask recheck: {error}", flush=True)
            self.reference_mask = None
            self.mask_settings = None

    def _build_ui(self, correction_name: str) -> None:
        title = QLabel("Consensus Assistant")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        subtitle = QLabel(f"Correction tool: {correction_name}")
        subtitle.setStyleSheet("color: #9aa0a6;")

        self.mode_tabs = QTabBar()
        self.mode_tabs.setExpanding(True)
        for mode in self.mode_names:
            self.mode_tabs.addTab("Tracking" if mode == "tracking" else "Mask")
        self.mode_tabs.setVisible(len(self.mode_names) > 1)
        self.mode_tabs.currentChanged.connect(self._change_mode)

        self.progress = QLabel()
        self.progress.setStyleSheet("font-weight: 600;")
        self.statistics = QLabel()
        self.statistics.setWordWrap(True)
        self.statistics.setStyleSheet(
            "padding: 6px; background-color: rgba(127, 127, 127, 0.12);"
        )
        self.site_filter = QComboBox()
        self.status_filter = QComboBox()
        self.status_filter.addItems(
            [
                NEEDS_ATTENTION,
                UNREVIEWED_STATUS,
                PROBLEM_FOUND,
                CORRECTED_STATUS,
                NO_ISSUE_STATUS,
                ALL_STATUSES,
            ]
        )
        self.site_filter.currentTextChanged.connect(self._filters_changed)
        self.status_filter.currentTextChanged.connect(self._filters_changed)
        filters = QFormLayout()
        filters.addRow("Site type", self.site_filter)
        filters.addRow("Status", self.status_filter)
        self.detail = QLabel()
        self.detail.setWordWrap(True)

        self.choices_box = QGroupBox()
        choices_layout = QVBoxLayout()
        self.choices = QLabel()
        self.choices.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.choices.setWordWrap(True)
        self.choices.setMinimumHeight(58)
        choices_layout.addWidget(self.choices)
        self.choices_box.setLayout(choices_layout)
        self.choices_box.setMinimumHeight(92)

        previous_button = QPushButton("Previous")
        previous_button.clicked.connect(self.previous)
        next_button = QPushButton("Next")
        next_button.clicked.connect(self.next_unresolved)
        self.no_issue_button = QPushButton("No issue")
        self.no_issue_button.setToolTip(
            "The marker was reviewed and the current result is acceptable."
        )
        self.no_issue_button.clicked.connect(self.mark_no_issue)
        self.problem_button = QPushButton("Problem found")
        self.problem_button.setToolTip(
            "The marker identifies a real problem that still needs correction."
        )
        self.problem_button.clicked.connect(self.mark_problem)
        self.corrected_button = QPushButton("Corrected")
        self.corrected_button.setToolTip(
            "The problem was corrected with the manual correction tool."
        )
        self.corrected_button.clicked.connect(self.mark_corrected)
        self.reopen_button = QPushButton("Reopen")
        self.reopen_button.clicked.connect(self.reopen)
        self.export_button = QPushButton("Export CSV")
        self.export_button.setToolTip(
            "Save the event-level report and the summary statistics."
        )
        self.export_button.clicked.connect(self.export_reports)
        self.recheck_button = QPushButton("Recheck current mask")
        self.recheck_button.clicked.connect(self.recheck_current_mask)

        self.show_all = QCheckBox("Show all matching markers")
        self.show_all.toggled.connect(self._set_all_markers_visible)

        nav = QHBoxLayout()
        nav.addWidget(previous_button)
        nav.addWidget(next_button)

        actions = QGridLayout()
        actions.addWidget(self.no_issue_button, 0, 0)
        actions.addWidget(self.problem_button, 0, 1)
        actions.addWidget(self.corrected_button, 1, 0)
        actions.addWidget(self.reopen_button, 1, 1)
        actions.addWidget(self.recheck_button, 2, 0, 1, 2)
        actions.addWidget(self.export_button, 3, 0, 1, 2)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #9aa0a6;")

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.mode_tabs)
        layout.addSpacing(4)
        layout.addWidget(self.statistics)
        layout.addLayout(filters)
        layout.addWidget(self.progress)
        layout.addWidget(self.detail)
        layout.addLayout(nav)
        layout.addWidget(self.choices_box)
        layout.addLayout(actions)
        layout.addWidget(self.show_all)
        layout.addStretch(1)
        layout.addWidget(self.note)
        self.setLayout(layout)
        self._populate_site_filter()
        self._apply_filters(refresh_view=False)
        self._update_mode_text()
        self._update_statistics()
        self._write_reports()

    def _update_mode_text(self) -> None:
        mask_mode = self.current_mode == "mask"
        self.choices_box.setTitle(
            "Mask comparison" if mask_mode else "Tracker choices"
        )
        self.recheck_button.setVisible(mask_mode)
        self.recheck_button.setEnabled(
            mask_mode
            and self.reference_mask is not None
            and self.primary_labels_layer is not None
        )
        self.note.setText(
            (
                "Edit the mask with the correction tool. The current site is "
                "rechecked after a label update."
            )
            if mask_mode
            else (
                "Inspect the track with the correction tool, then classify "
                "the site as No issue, Problem found, or Corrected."
            )
        )

    def _change_mode(self, index: int) -> None:
        if index < 0 or index >= len(self.mode_names):
            return
        self.current_mode = self.mode_names[index]
        self.mode_events = self._events_for_mode(self.current_mode)
        self.current_row = 0
        self._populate_site_filter()
        self._apply_filters(refresh_view=False)
        self._update_mode_text()
        self._update_statistics()
        self._refresh_marker_data()
        self._show_first_filtered()

    def _populate_site_filter(self) -> None:
        self.site_filter.blockSignals(True)
        self.site_filter.clear()
        if self.current_mode == "tracking":
            self.site_filter.addItems(
                [CONNECTION_SITES, UNTRACKED_SITES, ALL_SITES]
            )
        else:
            self.site_filter.addItems(
                [HIGH_PRIORITY_SITES, LOW_PRIORITY_SITES, ALL_SITES]
            )
        self.site_filter.blockSignals(False)

    def _filters_changed(self, value=None) -> None:
        self._apply_filters(refresh_view=True)

    def _apply_filters(self, refresh_view: bool) -> None:
        table = self.status_store.review_table(self.mode_events)
        site = self.site_filter.currentText()
        if self.current_mode == "tracking":
            if site == CONNECTION_SITES:
                table = table[table["reason"] == CONNECTION_DIFFERENCE]
            elif site == UNTRACKED_SITES:
                table = table[table["reason"] == PRIMARY_UNTRACKED]
        elif site == HIGH_PRIORITY_SITES:
            table = table[table["severity"].astype(str).str.lower() == "high"]
        elif site == LOW_PRIORITY_SITES:
            table = table[table["severity"].astype(str).str.lower() != "high"]

        status = self.status_filter.currentText()
        status_values = {
            UNREVIEWED_STATUS: (UNREVIEWED,),
            PROBLEM_FOUND: (PROBLEM,),
            CORRECTED_STATUS: (CORRECTED,),
            NO_ISSUE_STATUS: (NO_ISSUE,),
            NEEDS_ATTENTION: (UNREVIEWED, PROBLEM),
        }
        if status in status_values:
            table = table[table["classification"].isin(status_values[status])]
        self.events = table.reset_index(drop=True)
        self.current_row = min(self.current_row, max(len(self.events) - 1, 0))
        self._update_statistics()
        if refresh_view and hasattr(self, "all_markers"):
            self._refresh_marker_data()
            self._show_first_filtered()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.no_issue_button.setEnabled(enabled)
        self.problem_button.setEnabled(enabled)
        self.corrected_button.setEnabled(enabled)
        self.reopen_button.setEnabled(enabled)
        self.show_all.setEnabled(enabled)
        self.recheck_button.setEnabled(
            enabled
            and self.current_mode == "mask"
            and self.reference_mask is not None
            and self.primary_labels_layer is not None
        )

    def _event_points(self, events) -> np.ndarray:
        if not len(events):
            return np.empty((0, 3), dtype=float)
        return events[["frame", "object_y", "object_x"]].to_numpy(dtype=float)

    def _event_colors(self, events) -> list[str]:
        if not len(events):
            return []
        return [
            HIGH_COLOR if str(value).lower() == "high" else REVIEW_COLOR
            for value in events["severity"]
        ]

    def _add_marker_layers(self) -> None:
        unresolved = self.events
        self.all_markers = self.viewer.add_points(
            self._event_points(unresolved),
            name="Unresolved consensus sites",
            face_color=self._event_colors(unresolved) or HIGH_COLOR,
            border_color="white",
            border_width=0.12,
            size=12,
            symbol="ring",
            visible=False,
        )
        self.current_marker = self.viewer.add_points(
            np.empty((0, 3), dtype=float),
            name="Current consensus site",
            face_color="#00ffffff",
            border_color=CURRENT_COLOR,
            border_width=0.2,
            size=14,
            symbol="ring",
        )

    def _update_statistics(self) -> None:
        counts = self.status_store.statistics(self.mode_events)
        mode = "Tracking" if self.current_mode == "tracking" else "Mask"
        if self.current_mode == "tracking":
            reasons = self.mode_events["reason"].value_counts()
            categories = (
                f"{int(reasons.get(CONNECTION_DIFFERENCE, 0))} connection differences | "
                f"{int(reasons.get(PRIMARY_UNTRACKED, 0))} untracked objects"
            )
        else:
            severities = self.mode_events["severity"].astype(str).str.lower().value_counts()
            categories = (
                f"{int(severities.get('high', 0))} high priority | "
                f"{int(len(self.mode_events) - severities.get('high', 0))} low priority"
            )
        self.statistics.setText(
            f"{mode}: {categories}\n"
            f"{counts['to_review']} unreviewed | "
            f"{counts['problem_found']} problems\n"
            f"{counts['corrected']} corrected | "
            f"{counts['no_issue']} no issue\n"
            f"{counts['reviewed']}/{counts['initial_flagged']} reviewed | "
            f"showing {len(self.events)}"
        )

    def _write_reports(self):
        try:
            return self.status_store.export_reports(
                self.all_events, self.tracker_names
            )
        except OSError:
            self.note.setText(
                "The review was saved, but the report CSV is open in another "
                "program. Close the CSV and select Export CSV again."
            )
            return None

    def _refresh_marker_data(self) -> None:
        unresolved = self.events
        self.all_markers.data = self._event_points(unresolved)
        if len(unresolved):
            self.all_markers.face_color = self._event_colors(unresolved)

    def _set_all_markers_visible(self, visible: bool) -> None:
        self.all_markers.visible = bool(visible)

    def _current_event(self):
        return self.events.iloc[self.current_row]

    def _show_first_filtered(self) -> None:
        if not len(self.events):
            label = "tracking" if self.current_mode == "tracking" else "mask"
            self.progress.setText(f"No matching {label} sites")
            self.detail.setText("Change a filter to inspect other sites.")
            self.choices.clear()
            self.current_marker.data = np.empty((0, 3), dtype=float)
            self._set_controls_enabled(False)
            return
        self._set_controls_enabled(True)
        self.jump_to(self.current_row)

    def jump_to(self, row: int) -> None:
        if not len(self.events):
            return
        self.current_row = max(0, min(int(row), len(self.events) - 1))
        event = self._current_event()
        frame = int(event["frame"])
        self.viewer.dims.set_current_step(0, frame)
        self.viewer.camera.center = (
            float(event["object_y"]),
            float(event["object_x"]),
        )
        self.viewer.camera.zoom = 2.8
        self.current_marker.data = np.asarray(
            [[frame, float(event["object_y"]), float(event["object_x"])]],
            dtype=float,
        )

        classification = self.status_store.classification_for(
            str(event["event_id"])
        )
        resolved = classification in (NO_ISSUE, CORRECTED)
        status_labels = {
            UNREVIEWED: "Unreviewed",
            NO_ISSUE: "No issue",
            PROBLEM: "Problem found",
            CORRECTED: "Corrected",
        }
        label = "Mask site" if self.current_mode == "mask" else "Tracking site"
        self.progress.setText(
            f"{label} {self.current_row + 1} of {len(self.events)}"
            + f" | {status_labels[classification]}"
        )
        if self.current_mode == "mask":
            self._show_mask_event(event)
        else:
            self._show_tracking_event(event)
        self.no_issue_button.setEnabled(not resolved)
        self.problem_button.setEnabled(classification != PROBLEM and not resolved)
        self.corrected_button.setEnabled(not resolved)
        self.reopen_button.setEnabled(classification != UNREVIEWED)
        self.recheck_button.setEnabled(
            not resolved
            and self.current_mode == "mask"
            and self.reference_mask is not None
            and self.primary_labels_layer is not None
        )

    def _show_tracking_event(self, event) -> None:
        frame = int(event["frame"])
        primary_name = (
            self.tracker_names[0]
            if self.tracker_names
            else "primary tracker"
        )
        primary_choice = str(
            event.get(f"{primary_name}_successor", "")
        )
        if primary_choice == "ABSENT":
            explanation = (
                f"{primary_name} does not contain this segmented object."
            )
        elif primary_choice == "END":
            explanation = f"The {primary_name} track ends at this cell."
        else:
            explanation = (
                "The trackers selected different next-frame connections."
            )
        self.detail.setText(
            f"Frame {frame} | Mask label {int(event['mask_label'])}\n"
            f"{explanation}"
        )
        self.choices.setText(describe_choices(event, self.tracker_names))

    def _show_mask_event(self, event) -> None:
        reasons = {
            "primary_only_object": (
                "The primary mask contains an object with no reference match."
            ),
            "reference_only_object": (
                "The reference contains an object missing from the primary mask."
            ),
            "possible_split_or_merge": (
                "The masks contain a possible split or merge."
            ),
            "low_mask_agreement": (
                "The matched masks have low boundary agreement."
            ),
            "moderate_mask_agreement": (
                "The matched masks have moderate boundary agreement."
            ),
        }
        reason = reasons.get(
            str(event.get("reason", "")),
            "The primary and reference masks disagree.",
        )
        priority = (
            "High-priority review"
            if str(event.get("severity", "")).lower() == "high"
            else "Low-priority review"
        )
        self.detail.setText(
            f"Frame {int(event['frame'])} | "
            f"Mask label {int(event['mask_label'])}\n"
            f"{priority}: {reason}"
        )
        self.choices.setText(describe_mask_comparison(event))

    def previous(self) -> None:
        if len(self.events):
            self.jump_to((self.current_row - 1) % len(self.events))

    def next_unresolved(self) -> None:
        if not len(self.events):
            return
        self.jump_to((self.current_row + 1) % len(self.events))

    def _classify_current(self, classification: str, advance: bool) -> None:
        if not len(self.events):
            return
        self.status_store.set_classification(
            str(self._current_event()["event_id"]), classification
        )
        self._write_reports()
        old_row = self.current_row
        self._apply_filters(refresh_view=False)
        self._refresh_marker_data()
        if not len(self.events):
            self._show_first_filtered()
        elif advance:
            self.jump_to(min(old_row, len(self.events) - 1))
        else:
            self.jump_to(min(old_row, len(self.events) - 1))

    def mark_no_issue(self) -> None:
        self._classify_current(NO_ISSUE, advance=True)

    def mark_problem(self) -> None:
        self._classify_current(PROBLEM, advance=False)

    def mark_corrected(self) -> None:
        self._classify_current(CORRECTED, advance=True)

    def reopen(self) -> None:
        if not len(self.events):
            return
        self.status_store.set_classification(
            str(self._current_event()["event_id"]), UNREVIEWED
        )
        self._write_reports()
        self._apply_filters(refresh_view=True)

    def export_reports(self) -> None:
        paths = self._write_reports()
        if paths is None:
            return
        report_path, _ = paths
        self.note.setText(
            "Saved consensus_review_report.csv and "
            f"consensus_review_summary.csv in {report_path.parent}"
        )

    def _connect_primary_mask_layer(self) -> None:
        if self.reference_mask is None:
            return
        preferred = {"mask", "segm", "segmentation"}
        candidates = []
        for layer in self.viewer.layers:
            if layer.__class__.__name__.lower() != "labels":
                continue
            layer_data = getattr(layer, "data", np.empty(0))
            layer_shape = (
                (1, *layer_data.shape)
                if getattr(layer_data, "ndim", 0) == 2
                else tuple(layer_data.shape)
            )
            if tuple(layer_shape) != tuple(self.reference_mask.shape):
                continue
            priority = 0 if str(layer.name).lower() in preferred else 1
            candidates.append((priority, layer))
        if not candidates:
            return
        candidates.sort(key=lambda item: item[0])
        self.primary_labels_layer = candidates[0][1]
        self._mask_recheck_timer = QTimer(self)
        self._mask_recheck_timer.setSingleShot(True)
        self._mask_recheck_timer.setInterval(500)
        self._mask_recheck_timer.timeout.connect(self.recheck_current_mask)

        def schedule_recheck(event=None):
            if self.current_mode == "mask":
                self._mask_recheck_timer.start()

        try:
            self.primary_labels_layer.events.data.connect(schedule_recheck)
        except Exception as error:
            print(f"Cannot monitor mask changes: {error}", flush=True)
        self._update_mode_text()

    def recheck_current_mask(self) -> None:
        if (
            self.current_mode != "mask"
            or not len(self.events)
            or self.reference_mask is None
            or self.mask_settings is None
            or self.primary_labels_layer is None
        ):
            return
        event = self._current_event()
        event_id = str(event["event_id"])
        if event_id in self.status_store.resolved_ids():
            return
        frame = int(event["frame"])
        primary_data = np.asarray(self.primary_labels_layer.data)
        if primary_data.ndim == 2:
            primary_plane = primary_data
        else:
            primary_plane = primary_data[frame]
        reference_plane = np.asarray(self.reference_mask[frame])
        current_events = analyze_mask_frame(
            primary_plane,
            reference_plane,
            self.mask_settings,
            frame,
            self.mask_reference_model,
        )
        y = float(event["object_y"])
        x = float(event["object_x"])
        area = max(float(event.get("object_area", 0) or 0), 1.0)
        radius = max(5.0, 0.5 * math.sqrt(4.0 * area / math.pi))
        still_open = any(
            np.hypot(
                float(candidate["object_y"]) - y,
                float(candidate["object_x"]) - x,
            )
            <= radius
            for candidate in current_events
        )
        if not still_open:
            self.status_store.set_classification(event_id, CORRECTED)
            self._write_reports()
            self._apply_filters(refresh_view=True)
        else:
            self.progress.setText(
                f"Mask site {self.current_row + 1} of {len(self.events)} "
                "| Disagreement remains"
            )


def attach_consensus_review(
    viewer,
    output_dir: str | Path,
    correction_name: str,
    correction_dock=None,
    empty_plugin_dock=None,
):
    if load_review_data(output_dir) is None:
        return None
    panel = ConsensusReviewPanel(viewer, output_dir, correction_name)
    review_dock = viewer.window.add_dock_widget(
        panel,
        area="left",
        name="Consensus Assistant",
    )

    main_window = review_dock.parentWidget()
    native_left_docks = {
        dock.windowTitle(): dock
        for dock in main_window.findChildren(QDockWidget)
        if dock.windowTitle() in {"layer controls", "layer list"}
    }
    layer_controls = native_left_docks.get("layer controls")
    layer_list = native_left_docks.get("layer list")
    if layer_controls is not None and layer_list is not None:
        main_window.tabifyDockWidget(layer_controls, layer_list)
    tab_anchor = layer_controls or layer_list
    if tab_anchor is not None:
        main_window.tabifyDockWidget(tab_anchor, review_dock)

    if (
        empty_plugin_dock is not None
        and empty_plugin_dock is not correction_dock
    ):
        empty_plugin_dock.hide()
    if correction_dock is not None:
        main_window.addDockWidget(Qt.RightDockWidgetArea, correction_dock)
        main_window.resizeDocks(
            [review_dock, correction_dock],
            [400, 400],
            Qt.Horizontal,
        )

    review_dock.raise_()
    QTimer.singleShot(0, review_dock.raise_)
    return panel
