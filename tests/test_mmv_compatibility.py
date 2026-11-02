import ast
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import installer
import launch_biotrack_studio as launcher


ROOT = Path(__file__).resolve().parents[1]


def mmv_template():
    tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "c_h4tracks" for target in node.targets))


class MMVInstallationTests(unittest.TestCase):
    def run_install(self, tool, compatible, hardware="cpu", exists=True, imports_ready=True):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(installer, "get_conda_cmd", return_value="conda"))
            stack.enter_context(patch.object(installer, "get_hardware", return_value=hardware))
            stack.enter_context(patch.object(installer, "env_exists", return_value=exists))
            stack.enter_context(patch.object(installer, "_imports_ready", return_value=imports_ready))
            stack.enter_context(patch.object(installer.subprocess, "run"))
            stack.enter_context(patch.object(installer, "get_subprocess_env", return_value={}))
            check = stack.enter_context(patch.object(installer, "mmv_dependencies_compatible", return_value=compatible))
            pip = stack.enter_context(patch.object(installer, "_pip_install"))
            stack.enter_context(patch.object(installer, "_verify_imports"))
            stack.enter_context(patch.object(installer, "ensure_user_dirs"))
            stack.enter_context(patch.object(installer, "USER_ALGO_DIR", Path(temporary)))
            stack.enter_context(patch.object(installer, "load_registry", return_value={}))
            stack.enter_context(patch.object(installer, "save_registry"))
            installer.install_correction_plugin(tool, "pandas==2.2.3", "# launcher")
            return pip.call_count, check.call_count

    def test_importable_mmv_with_incompatible_pandas_is_repaired(self):
        self.assertEqual(self.run_install("MMV_H4Tracks", False), (1, 1))

    def test_compatible_mmv_environment_is_reused(self):
        self.assertEqual(self.run_install("MMV_H4Tracks", True), (0, 1))

    def test_other_correction_environment_is_not_changed(self):
        self.assertEqual(self.run_install("napari-amdtrk", False), (0, 0))

    def test_pandas_repair_keeps_an_importable_existing_torch(self):
        self.assertEqual(self.run_install("MMV_H4Tracks", False, hardware="cuda"), (1, 1))

    def test_fresh_nvidia_environment_gets_the_compatible_torch_build(self):
        self.assertEqual(self.run_install("MMV_H4Tracks", False, hardware="cuda",
                                         exists=False, imports_ready=False), (2, 0))

    def test_launcher_does_not_skip_mmv_dependency_repair(self):
        with patch.object(launcher, "run_capture", return_value="READY\n"), patch.object(
            launcher, "env_imports_ready", return_value=True
        ), patch.object(
            installer, "stardist_dependencies_compatible", return_value=True
        ), patch.object(launcher, "log"), patch.object(installer, "mmv_dependencies_compatible", return_value=False):
            self.assertFalse(launcher.algorithms_ready("conda"))

    def test_version_check_rejects_failed_dependency_probe(self):
        with patch.object(installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "conda")):
            self.assertFalse(installer.mmv_dependencies_compatible("conda", "env_correction_mmv_h4tracks"))


@unittest.skipUnless(importlib.util.find_spec("mmv_h4tracks"), "Run inside an MMV correction environment")
class MMVNativeCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows extended-path compatibility")
    def test_windows_long_path_save_reopen(self):
        import napari
        from mmv_h4tracks import _reader, _widget, _writer

        namespace = {"__name__": "mmv_long_path_test"}
        exec(compile(mmv_template(), "c_h4tracks", "exec"), namespace)
        namespace["_configure_mmv_long_paths"]()
        configured_writer = _widget.save_zarr
        namespace["_configure_mmv_long_paths"]()
        self.assertIs(_widget.save_zarr, configured_writer)
        mask = np.zeros((3, 32, 32), np.uint16)
        mask[:, 10:14, 10:14] = 1
        viewer = napari.Viewer(show=True)
        try:
            raw = viewer.add_image(mask.astype(np.uint8))
            labels = viewer.add_labels(mask)
            tracks = viewer.add_tracks(np.array([[1, frame, 11, 11] for frame in range(3)]))
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / ("model_" + "a" * 50) / ("result_" + "b" * 35) / "corrected.zarr"
                while len(str(output / "segmentation_data/c/0/0/0.0123456789abcdef0123456789abcdef.partial")) <= 260:
                    output = output.parent / ("nested_" + "c" * 20) / output.name
                output.parent.mkdir(parents=True)
                self.assertGreater(len(str(output / "segmentation_data/c/0/0/0.0123456789abcdef0123456789abcdef.partial")), 260)
                with patch.object(_writer, "notify"):
                    _widget.save_zarr(str(output), [raw, labels, tracks])
                reopened, _ = _reader.zarr_reader(str(output))
                np.testing.assert_array_equal(reopened["segmentation_data"][:], mask)
                self.assertEqual(len(reopened["tracking_data"][:]), 3)
        finally:
            viewer.close()

    def test_centroid_update_and_native_save_reopen(self):
        import napari
        import pandas as pd
        from mmv_h4tracks._reader import load_zarr_data, zarr_reader
        from mmv_h4tracks._writer import save_zarr

        self.assertEqual(pd.__version__, "2.2.3")
        namespace = {"__name__": "mmv_compatibility_test"}
        exec(compile(mmv_template(), "c_h4tracks", "exec"), namespace)
        mask = np.zeros((5, 64, 64), np.uint16)
        mask[:, 20:25, 20:25] = 1
        image = mask.astype(np.uint8) * 100
        data = np.array([[1, frame, 22, 22] for frame in range(5)], dtype=float)
        viewer = napari.Viewer(show=True)
        try:
            raw = viewer.add_image(image, name="Raw")
            labels = viewer.add_labels(mask.copy(), name="Mask")
            with tempfile.TemporaryDirectory() as input_folder:
                pd.DataFrame(data, columns=["trackId", "frame", "Center_of_the_object_1", "Center_of_the_object_0"]).to_csv(
                    Path(input_folder) / "track.csv", index=False
                )
                namespace["add_tracks_layer"](viewer, input_folder, mask)
            tracks = viewer.layers["Tracks"]
            dock, _ = viewer.window.add_plugin_dock_widget(plugin_name="mmv_h4tracks")
            widget = dock.widget()
            namespace["initialize_mmv_workspace"](widget, mask)
            labels.data[2] = 0
            labels.data[2, 21:28, 20:27] = 1
            self.assertFalse(np.array_equal(widget.initial_layers[0], labels.data))
            widget.tracking_window.update_all_centroids()
            self.assertEqual(len(tracks.data), 5)
            updated = tracks.data[tracks.data[:, 1] == 2][0]
            self.assertFalse(np.array_equal(updated[2:], [22, 22]))
            self.assertEqual(labels.data[2, int(updated[2]), int(updated[3])], 1)
            with tempfile.TemporaryDirectory() as temporary:
                destination = str(Path(temporary) / "corrected.zarr")
                with patch("mmv_h4tracks._writer.notify"):
                    save_zarr(destination, [raw, labels, tracks])
                group, _ = zarr_reader(destination)
                raw_levels, saved_mask, saved_tracks, _ = load_zarr_data(group)
                np.testing.assert_array_equal(raw_levels[0], image)
                np.testing.assert_array_equal(saved_mask, labels.data)
                np.testing.assert_array_equal(saved_tracks, tracks.data)
            print(json.dumps({"native_mmv_centroid_update": True, "native_save_reopen": True,
                              "napari": napari.__version__, "pandas": pd.__version__}), flush=True)
        finally:
            viewer.close()


if __name__ == "__main__":
    unittest.main()
