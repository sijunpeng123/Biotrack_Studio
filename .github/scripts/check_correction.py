"""Exercise the installed correction plugin, native saving and review persistence."""
from __future__ import annotations

import argparse
import ast
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def run(tool, output):
    import napari
    import numpy as np
    import pandas as pd
    import tifffile

    output.mkdir(parents=True, exist_ok=True)
    for name in ("_runtime_helpers.py", "mask_consensus.py", "consensus_review_model.py", "consensus_review_panel.py"):
        shutil.copy2(ROOT / "biotrack_studio" / name, output / name)
    sys.path.insert(0, str(output))
    from consensus_review_panel import ConsensusReviewPanel
    from consensus_review_model import NO_ISSUE

    mask = np.zeros((5, 64, 64), dtype=np.uint16)
    mask[:, 20:25, 20:25] = 1
    image = mask.astype(np.uint8) * 100
    for name, data in (("image.tif", image), ("mask.tif", mask)):
        tifffile.imwrite(output / name, data, imagej=True, photometric="minisblack", metadata={"axes": "TYX"})
    rows = pd.DataFrame({"frame": range(5), "trackId": 1, "state": "none", "continuous_label": 1,
                         "Center_of_the_object_0": 22, "Center_of_the_object_1": 22})
    rows.to_csv(output / "track.csv", index=False)
    (output / "config.yaml").write_text("intensity_suffix: image\nmask_suffix: mask\ntrack_suffix: track\nframe_base: 0\nstateCol: state\n", encoding="utf-8")
    pd.DataFrame([{"event_id": "B_END_00002_1", "frame": 2, "mask_label": 1,
                   "object_y": 22, "object_x": 22, "reason": "interior_track_end",
                   "ultrack_successor": "END", "trackpy_successor": "END",
                   "edge_distance_px": 20, "edge_margin_px": 15}]).to_csv(output / "consensus_events.csv", index=False)

    tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
    template = "c_h4tracks" if tool == "mmv" else "c_amdtrk"
    code = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == template for target in node.targets))
    namespace = {"__name__": "correction_launch_smoke"}
    exec(compile(code, template, "exec"), namespace)
    viewer = napari.Viewer(show=False)
    try:
        if tool == "mmv":
            from mmv_h4tracks._writer import save_zarr
            from mmv_h4tracks._reader import zarr_reader, load_zarr_data
            from qtpy.QtCore import QTimer
            from qtpy.QtWidgets import QApplication, QMessageBox

            raw = viewer.add_image(image, name="Raw")
            labels = viewer.add_labels(mask.copy(), name="Mask")
            namespace["add_tracks_layer"](viewer, str(output), mask)
            dock, _ = viewer.window.add_plugin_dock_widget(plugin_name="mmv_h4tracks")
            plugin = dock.widget()
            namespace["initialize_mmv_workspace"](plugin, mask)
            plugin.tracking_window.update_all_centroids()
            tracks = viewer.layers["Tracks"]
            assert len(tracks.data) == 5
            saved = str(output / "corrected.zarr")
            QTimer.singleShot(0, lambda: [window.accept() for window in QApplication.topLevelWidgets()
                                         if isinstance(window, QMessageBox)])
            save_zarr(saved, [raw, labels, tracks])
            group, _ = zarr_reader(saved)
            raw_levels, saved_mask, saved_tracks, _ = load_zarr_data(group)
            np.testing.assert_array_equal(raw_levels[0], image)
            np.testing.assert_array_equal(saved_mask, mask)
            np.testing.assert_array_equal(saved_tracks, tracks.data)
            plugin_version = version("mmv-h4tracks")
        else:
            from napari_amdtrk._reader import reader_function
            from napari_amdtrk._widget import AmdTrkWidget

            for layer in reader_function(str(output)):
                namespace["add_layer_from_reader"](viewer, layer)
            widget = AmdTrkWidget(viewer)
            widget.create_or_replace(1, 2, None)
            widget.save(mask_flag=True)
            reopened = next(layer for layer in reader_function(str(output)) if layer[2] == "tracks")
            saved = reopened[1]["metadata"]["ori_data"]
            assert len(saved) == 5
            assert saved.loc[saved.frame == 2, "trackId"].iloc[0] != 1
            assert not saved.duplicated(["frame", "continuous_label"]).any()
            np.testing.assert_array_equal(tifffile.imread(output / "mask.tif"), mask)
            plugin_version = version("napari-amdtrk")

        panel = ConsensusReviewPanel(viewer, output, tool)
        panel.mark_no_issue()
        assert panel.status_store.classification_for("B_END_00002_1") == NO_ISSUE
        panel.close()
        reopened = ConsensusReviewPanel(viewer, output, tool)
        assert reopened.status_store.classification_for("B_END_00002_1") == NO_ISSUE
        assert (output / "consensus_review_report.csv").is_file()
        reopened.close()
        report = {"tool": tool, "version": plugin_version, "native_save_reopen": True,
                  "review_state_reopen": True, "pandas": version("pandas"), "napari": version("napari")}
        (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
    finally:
        viewer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", choices=("mmv", "amdtrk"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.output:
        run(args.tool, args.output.resolve())
    else:
        with tempfile.TemporaryDirectory() as temporary:
            run(args.tool, Path(temporary))


if __name__ == "__main__":
    main()
