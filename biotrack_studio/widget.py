import json, os, subprocess, napari, numpy as np, tifffile
from pathlib import Path
from magicgui import magic_factory
from .engine import SafeRunner
from .env_manager import get_env_cmd, get_subprocess_env
from .paths import load_registry, resolve_algorithm_script
from .utils import prepare_standard_tif
from .consensus_assist import select_correction_target
from .mask_consensus import (
    AUTOMATIC_REFERENCE,
    MaskConsensusSettings,
    build_mask_assisted_correction,
)

os.environ.setdefault("PYTHONNOUSERSITE", "1")

_AUTO_TEXT_SIZE_VIEWERS = set()

CORRECTION_PLACEHOLDER = "Select correction tool..."
SKIP_SEGMENTATION = "Skip segmentation, use existing mask.tif"
SKIP_TRACKING = "Skip tracking"
DEFAULT_SEGMENTATION = "cellpose_cyto"
DEFAULT_TRACKING = "sctrack"
SCTRACK_MIN_FRAMES = 13
AUTO_PRIMARY_TRACKER = "Auto-detect from result folder"
RUN_METADATA_NAME = "BioTrack_Studio_run.json"
MASK_MODE_AUTOMATIC = "Automatic"
MASK_MODE_CUSTOM = "Custom"


def _estimate_text_size(layer):
    try:
        count = len(layer.data)
    except Exception:
        count = 0

    if count > 300:
        return 4
    if count > 120:
        return 6
    return 8


def _apply_auto_text_size(layer):
    text = getattr(layer, "text", None)
    if text is None:
        return

    try:
        text.size = _estimate_text_size(layer)
    except Exception:
        pass


def _enable_auto_text_size(viewer):
    viewer_id = id(viewer)
    if viewer_id in _AUTO_TEXT_SIZE_VIEWERS:
        for layer in viewer.layers:
            _apply_auto_text_size(layer)
        return

    _AUTO_TEXT_SIZE_VIEWERS.add(viewer_id)

    @viewer.layers.events.inserted.connect
    def _on_layer_inserted(event):
        _apply_auto_text_size(event.value)

    for layer in viewer.layers:
        _apply_auto_text_size(layer)


def get_algos(cat):
    try:
        reg = load_registry()
        installed = []
        for name, info in reg.get(cat, {}).items():
            script = resolve_algorithm_script(info.get("script", ""))
            if os.path.exists(script):
                installed.append(name)
        if cat == "segmentation":
            return [SKIP_SEGMENTATION] + installed
        if cat == "tracking":
            return [SKIP_TRACKING] + installed
        return installed
    except Exception:
        if cat == "segmentation":
            return [SKIP_SEGMENTATION]
        if cat == "tracking":
            return [SKIP_TRACKING]
        return []

def get_correction_methods(*args):
    methods = [CORRECTION_PLACEHOLDER]
    try:
        registry = load_registry()
        methods.extend(registry.get("correction", {}).keys())
    except Exception:
        pass
    return methods

def get_primary_tracking_methods(*args):
    methods = [AUTO_PRIMARY_TRACKER]
    try:
        registry = load_registry()
        methods.extend(
            name
            for name, info in registry.get("tracking", {}).items()
            if name.lower() != "trackpy"
            and os.path.exists(resolve_algorithm_script(info.get("script", "")))
        )
    except Exception:
        pass
    return methods

def get_mask_reference_methods(*args):
    methods = [AUTOMATIC_REFERENCE]
    try:
        registry = load_registry()
        methods.extend(
            name
            for name, info in registry.get("segmentation", {}).items()
            if os.path.exists(resolve_algorithm_script(info.get("script", "")))
        )
    except Exception:
        pass
    return methods

def _write_run_metadata(out_dir: str, seg_method: str, track_method: str):
    path = Path(out_dir) / RUN_METADATA_NAME
    temporary = path.with_suffix(".tmp")
    payload = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    if seg_method != SKIP_SEGMENTATION:
        payload["segmentation_method"] = seg_method
    if track_method != SKIP_TRACKING:
        payload["tracking_method"] = track_method
    elif seg_method != SKIP_SEGMENTATION:
        payload.pop("tracking_method", None)
    temporary.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)

def _detect_primary_tracker(out_dir: str, selected: str):
    if selected != AUTO_PRIMARY_TRACKER:
        return selected
    path = Path(out_dir) / RUN_METADATA_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("tracking_method")
    except Exception:
        return None
    return value if isinstance(value, str) and value else None

def validate_pipeline_selection(seg_method: str, track_method: str):
    if seg_method == SKIP_SEGMENTATION and track_method == SKIP_TRACKING:
        return "Select at least one pipeline step."
    return None


def validate_pipeline_output(seg_method: str, track_method: str, output_dir: str | Path):
    if (
        seg_method != SKIP_SEGMENTATION
        and track_method == SKIP_TRACKING
        and (Path(output_dir) / "track.csv").is_file()
    ):
        return (
            "This folder already contains track.csv. Select an empty output "
            "folder before running segmentation without tracking."
        )
    return None


def validate_reused_mask(image_path: str | Path, output_dir: str | Path):
    mask_path = Path(output_dir) / "mask.tif"
    if not mask_path.is_file():
        return "Skipping segmentation requires an existing mask.tif in the output folder."
    try:
        with tifffile.TiffFile(image_path) as image_tif:
            image_shape = tuple(int(value) for value in image_tif.series[0].shape)
        with tifffile.TiffFile(mask_path) as mask_tif:
            mask_shape = tuple(int(value) for value in mask_tif.series[0].shape)
            mask_dtype = np.dtype(mask_tif.series[0].dtype)
        if image_shape != mask_shape:
            return (
                f"The existing mask.tif shape {mask_shape} does not match the "
                f"input image shape {image_shape}."
            )
        if mask_dtype.kind not in "iu":
            return "The existing mask.tif must contain integer cell labels."
        if not np.any(tifffile.memmap(mask_path)):
            return "The existing mask.tif contains no labelled cells."
    except Exception as error:
        return f"The existing mask.tif could not be validated: {error}"
    return None


def validate_tracking_sequence(track_method: str, image_path: str | Path):
    if str(track_method).lower() != "sctrack":
        return None
    with tifffile.TiffFile(image_path) as tif:
        shape = tuple(int(value) for value in tif.series[0].shape)
    frame_count = shape[0] if len(shape) >= 3 else 1
    if frame_count < SCTRACK_MIN_FRAMES:
        return (
            "SC-Track requires at least 13 frames. Select another tracking "
            "method or provide a longer sequence."
        )
    return None

def validate_correction_selection(correction_method: str):
    if correction_method == CORRECTION_PLACEHOLDER:
        return "Select a correction tool."
    return None

def _find_env_python(env_name: str):
    try:
        result = subprocess.run(
            [get_env_cmd(), "env", "list", "--json"],
            check=True,
            capture_output=True,
            text=True,
            env=get_subprocess_env(),
        )
        envs = json.loads(result.stdout).get("envs", [])
    except Exception:
        return None

    for env_path in envs:
        prefix = Path(env_path)
        if prefix.name.lower() != env_name.lower():
            continue
        python_path = prefix / ("python.exe" if os.name == "nt" else "bin/python")
        if python_path.exists():
            return str(python_path)
    return None

def _correction_subprocess_env():
    env = get_subprocess_env()
    for key in ("QT_API", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYQTGRAPH_QT_LIB"):
        env.pop(key, None)
    return env

def _correction_creation_kwargs():
    kwargs = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs

def _launch_external_correction(out_dir: str, correction_method: str):
    registry = load_registry()
    info = registry.get("correction", {}).get(correction_method)
    if not info:
        return False

    script = resolve_algorithm_script(info.get("script", ""))
    if not os.path.exists(script):
        from napari.utils.notifications import show_error
        show_error(f"Correction launcher not found: {correction_method}")
        return True

    env_name = info.get("env")
    if not env_name:
        from napari.utils.notifications import show_error
        show_error(f"Correction environment not registered: {correction_method}")
        return True

    env_python = _find_env_python(env_name)
    if env_python:
        cmd = [env_python, script, out_dir]
    else:
        cmd = [get_env_cmd(), "run", "--no-capture-output", "-n", env_name, "python", script, out_dir]

    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in correction_method)
    log_path = os.path.join(out_dir, f"BioTrack_Studio_correction_{safe_name}.log")
    try:
        with open(log_path, "a", encoding="utf-8", buffering=1) as log_file:
            log_file.write("\n[BioTrack Studio] Starting manual correction:\n")
            log_file.write(" ".join(cmd) + "\n")
            subprocess.Popen(
                cmd,
                env=_correction_subprocess_env(),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                **_correction_creation_kwargs(),
            )
        from napari.utils.notifications import show_info
        show_info(f"Started manual correction: {correction_method}")
    except Exception as e:
        from napari.utils.notifications import show_error
        show_error(f"Manual Correction Error: {e}")
    return True

def _open_correction(viewer: napari.Viewer, out_dir: str, correction_method: str):
    if _launch_external_correction(out_dir, correction_method):
        return

    from napari.utils.notifications import show_error
    show_error(f"Manual correction tool is not installed: {correction_method}")

@magic_factory(call_button="Run Analysis", 
               input_path={"label": "Input Data", "mode": "d"}, 
               output_dir={"label": "Output Folder", "mode": "d"}, 
               seg_method={"choices": lambda *args: get_algos("segmentation"), "label": "Segmentation"}, 
               track_method={"choices": lambda *args: get_algos("tracking"), "label": "Tracking"})
def make_cell_widget(
    viewer: napari.Viewer,
    input_path: Path,
    output_dir: Path,
    seg_method: str = DEFAULT_SEGMENTATION,
    track_method: str = DEFAULT_TRACKING,
):
    _enable_auto_text_size(viewer)

    selection_error = validate_pipeline_selection(seg_method, track_method)
    if selection_error:
        from napari.utils.notifications import show_error
        show_error(selection_error)
        return

    out_dir = str(output_dir)
    os.makedirs(out_dir, exist_ok=True)
    output_error = validate_pipeline_output(seg_method, track_method, out_dir)
    if output_error:
        from napari.utils.notifications import show_error
        show_error(output_error)
        return
    try: std_img = prepare_standard_tif(str(input_path), out_dir)
    except Exception as e:
        from napari.utils.notifications import show_error
        show_error(f"Data Error: {e}"); return

    if seg_method == SKIP_SEGMENTATION:
        mask_error = validate_reused_mask(std_img, out_dir)
        if mask_error:
            from napari.utils.notifications import show_error
            show_error(mask_error)
            return

    sequence_error = validate_tracking_sequence(track_method, std_img)
    if sequence_error:
        from napari.utils.notifications import show_error
        show_error(sequence_error)
        return

    registry = load_registry()
    
    s_info = registry["segmentation"].get(seg_method)
    if s_info and seg_method != SKIP_SEGMENTATION:
        script = resolve_algorithm_script(s_info["script"])
        if not os.path.exists(script):
            from napari.utils.notifications import show_error
            show_error(f"Algorithm script not found: {seg_method}")
            return
        if not SafeRunner.run_step(s_info["env"], script, [std_img, out_dir], seg_method):
            return
        _write_run_metadata(out_dir, seg_method, SKIP_TRACKING)

    t_info = registry["tracking"].get(track_method)
    if t_info and track_method != SKIP_TRACKING:
        script = resolve_algorithm_script(t_info["script"])
        if not os.path.exists(script):
            from napari.utils.notifications import show_error
            show_error(f"Algorithm script not found: {track_method}")
            return
        if not os.path.exists(os.path.join(out_dir, "mask.tif")):
            from napari.utils.notifications import show_error
            show_error("Tracking requires mask.tif. Run a successful segmentation first.")
            return
        if not SafeRunner.run_step(t_info["env"], script, [out_dir], track_method):
            return
        _write_run_metadata(out_dir, seg_method, track_method)

    if os.path.exists(std_img): viewer.add_image(tifffile.imread(std_img), name="Raw")
    if os.path.exists(os.path.join(out_dir, "mask.tif")): viewer.add_labels(tifffile.imread(os.path.join(out_dir, "mask.tif")), name="Mask")


def _configure_correction_widget(widget):
    def update_visibility(*_):
        widget.primary_tracker.visible = bool(
            widget.consensus_assistance.value
        )
        mask_enabled = bool(widget.mask_consensus_assistance.value)
        widget.mask_reference_model.visible = mask_enabled
        widget.mask_consensus_mode.visible = mask_enabled
        custom_enabled = (
            mask_enabled
            and widget.mask_consensus_mode.value == MASK_MODE_CUSTOM
        )
        widget.mask_centroid_ratio.visible = custom_enabled
        widget.mask_review_iou.visible = custom_enabled
        widget.mask_priority_iou.visible = custom_enabled

    widget.consensus_assistance.changed.connect(update_visibility)
    widget.mask_consensus_assistance.changed.connect(update_visibility)
    widget.mask_consensus_mode.changed.connect(update_visibility)
    update_visibility()


@magic_factory(call_button="Open Correction Workspace",
               widget_init=_configure_correction_widget,
               result_dir={"label": "Result Folder", "mode": "d"},
               correction_method={"choices": get_correction_methods, "label": "Correction Tool"},
               consensus_assistance={"label": "Tracking consensus assistance"},
               primary_tracker={"choices": get_primary_tracking_methods, "label": "Primary Tracking Result"},
               mask_consensus_assistance={"label": "Mask consensus assistance"},
               mask_reference_model={"choices": get_mask_reference_methods, "label": "Mask Reference"},
               mask_consensus_mode={"choices": [MASK_MODE_AUTOMATIC, MASK_MODE_CUSTOM], "label": "Mask Settings"},
               mask_centroid_ratio={"label": "Centroid Ratio (Custom)", "min": 0.25, "max": 1.0, "step": 0.25},
               mask_review_iou={"label": "Low-priority IoU (Custom)", "min": 0.05, "max": 0.95, "step": 0.05},
               mask_priority_iou={"label": "High-priority IoU (Custom)", "min": 0.0, "max": 0.9, "step": 0.05})
def make_correction_widget(
    viewer: napari.Viewer,
    result_dir: Path,
    correction_method: str = CORRECTION_PLACEHOLDER,
    consensus_assistance: bool = False,
    primary_tracker: str = AUTO_PRIMARY_TRACKER,
    mask_consensus_assistance: bool = False,
    mask_reference_model: str = AUTOMATIC_REFERENCE,
    mask_consensus_mode: str = MASK_MODE_AUTOMATIC,
    mask_centroid_ratio: float = 0.50,
    mask_review_iou: float = 0.70,
    mask_priority_iou: float = 0.50,
):
    _enable_auto_text_size(viewer)
    out_dir = str(result_dir)
    if not os.path.isdir(out_dir):
        from napari.utils.notifications import show_error
        show_error("Result folder does not exist.")
        return
    selection_error = validate_correction_selection(correction_method)
    if selection_error:
        from napari.utils.notifications import show_error
        show_error(selection_error)
        return
    correction_dir = out_dir
    if consensus_assistance or mask_consensus_assistance:
        from qtpy.QtCore import Qt
        from qtpy.QtWidgets import QApplication, QProgressDialog
        from napari.utils.notifications import show_info, show_warning

        if mask_consensus_mode == MASK_MODE_CUSTOM:
            try:
                mask_settings = MaskConsensusSettings(
                    centroid_ratio=float(mask_centroid_ratio),
                    review_iou=float(mask_review_iou),
                    priority_iou=float(mask_priority_iou),
                )
                mask_settings.validate()
            except ValueError as error:
                show_warning(str(error))
                return
        else:
            mask_settings = MaskConsensusSettings()

        progress = QProgressDialog(
            "Preparing consensus assistance...",
            "Cancel",
            0,
            0,
            QApplication.activeWindow(),
        )
        progress.setWindowTitle("Consensus Assistance")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        def report_progress(message):
            viewer.status = message
            progress.setLabelText(message)
            QApplication.processEvents()

        assistance_dir = None
        progress.show()
        try:
            if consensus_assistance:
                primary = _detect_primary_tracker(out_dir, primary_tracker)
                if not primary:
                    show_warning(
                        "The primary tracking method could not be detected. "
                        "Tracking assistance was skipped."
                    )
                else:
                    show_info(
                        "Preparing TrackPy-assisted correction. "
                        "The original track is preserved."
                    )
                    selection = select_correction_target(
                        out_dir,
                        primary,
                        True,
                        callback=report_progress,
                        cancel_check=progress.wasCanceled,
                    )
                    correction_dir = selection.correction_dir
                    assistance_dir = selection.assistance_dir
                    if selection.warning:
                        show_warning(selection.warning)

            if mask_consensus_assistance and not progress.wasCanceled():
                try:
                    summary = build_mask_assisted_correction(
                        out_dir,
                        existing_correction_dir=(
                            correction_dir if correction_dir != out_dir else None
                        ),
                        assistance_root=assistance_dir,
                        reference_model=mask_reference_model,
                        settings=mask_settings,
                        callback=report_progress,
                        cancel_check=progress.wasCanceled,
                    )
                    correction_dir = summary["correction_dir"]
                    show_info(
                        "Mask assistance is ready. "
                        "The original mask is preserved."
                    )
                except Exception as error:
                    show_warning(
                        f"Mask consensus assistance was skipped: {error}"
                    )
        finally:
            progress.close()

        if correction_dir != out_dir:
            show_info(
                "Consensus assistance is ready. "
                "Opening the primary results with review markers."
            )
    _open_correction(viewer, correction_dir, correction_method)
