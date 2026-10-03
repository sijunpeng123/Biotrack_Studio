import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import tifffile

from biotrack_studio import consensus_assist, store_widget, widget


class AlgorithmStoreTests(unittest.TestCase):
    def run_inline_tracking_adapter(self, algo_id, info, masks):
        normalized = store_widget._validate_algo_config(algo_id, info)
        script_content = store_widget._build_inline_tracking_script(algo_id, normalized)
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            script_path = output_dir / "adapter.py"
            script_path.write_text(script_content, encoding="utf-8")
            tifffile.imwrite(output_dir / "mask.tif", masks.astype(np.uint16))
            result = subprocess.run(
                [sys.executable, str(script_path), str(output_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            standard = pd.read_csv(output_dir / "track.csv") if result.returncode == 0 else None
            native_path = output_dir / "tracking_output" / f"{algo_id}_native_track.csv"
            native = pd.read_csv(native_path) if native_path.exists() else None
            if standard is not None:
                valid_nodes = {
                    (frame, int(label))
                    for frame, mask_frame in enumerate(masks)
                    for label in np.unique(mask_frame)
                    if int(label) > 0
                }
                consensus_assist.validate_track_file(
                    output_dir / "track.csv",
                    masks,
                    valid_nodes,
                )
            return result, standard, native, script_content

    def test_catalog_rejects_unsafe_algorithm_id(self):
        info = {
            "name": "Unsafe",
            "category": "segmentation",
            "dependencies": [],
            "description": "test",
            "core_code": "final_mask = img",
        }
        with self.assertRaises(ValueError):
            store_widget._validate_algo_config("../unsafe", info)

    def test_script_download_requires_checksum(self):
        info = {
            "name": "Script",
            "category": "segmentation",
            "dependencies": [],
            "description": "test",
            "script_url": "https://example.org/adapter.py",
        }
        with self.assertRaises(ValueError):
            store_widget._validate_algo_config("script_test", info)

    def test_catalog_requires_exactly_one_adapter_source(self):
        info = {
            "name": "Ambiguous",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "core_code": "final_track_df = pd.DataFrame()",
            "script_url": "https://example.org/adapter.py",
            "sha256": "0" * 64,
        }
        with self.assertRaisesRegex(ValueError, "exactly one"):
            store_widget._validate_algo_config("ambiguous", info)

    def test_adapter_script_commit_preserves_literal_backslash_n(self):
        with tempfile.TemporaryDirectory() as temporary:
            script_path = Path(temporary) / "adapter.py"
            content = 'message = "\\n"\nvalue = 1'
            store_widget._commit_adapter_script(script_path, content)
            self.assertEqual(script_path.read_text(encoding="utf-8"), content + "\n")

            with self.assertRaises(SyntaxError):
                store_widget._commit_adapter_script(script_path, "if")
            self.assertEqual(script_path.read_text(encoding="utf-8"), content + "\n")

    def test_inline_adapter_configuration_is_validated(self):
        base = {
            "name": "Adapter",
            "category": "tracking",
            "dependencies": ["pandas==2.2.3"],
            "description": "test",
            "core_code": "final_track_df = pd.DataFrame()",
        }
        normalized = store_widget._validate_algo_config("adapter", base)
        self.assertEqual(normalized["frame_base"], 0)
        self.assertEqual(normalized["dependencies"].count("pandas==2.2.3"), 1)
        self.assertIn("numpy", normalized["dependencies"])
        self.assertIn("tifffile", normalized["dependencies"])
        self.assertIn("pyyaml", normalized["dependencies"])

        with self.assertRaisesRegex(ValueError, "frame_base"):
            store_widget._validate_algo_config("adapter", {**base, "frame_base": 2})
        with self.assertRaisesRegex(ValueError, "unsupported column"):
            store_widget._validate_algo_config(
                "adapter",
                {**base, "column_map": {"source_id": "unsupported"}},
            )

        script_entry = {
            "name": "Script adapter",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "script_url": "https://example.org/adapter.py",
            "sha256": "0" * 64,
            "frame_base": 1,
        }
        with self.assertRaisesRegex(ValueError, "inline tracking"):
            store_widget._validate_algo_config("script_adapter", script_entry)

    def test_inline_tracking_preserves_state_and_derives_mask_labels(self):
        masks = np.zeros((2, 16, 16), dtype=np.uint16)
        masks[0, 3:7, 3:7] = 4
        masks[1, 4:8, 4:8] = 7
        info = {
            "name": "State tracker",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "core_code": """final_track_df = pd.DataFrame([
    {'frame': 0, 'track_id': 0, 'x': 4.5, 'y': 4.5, 'state': 'G1', 'score': 0.9},
    {'frame': 1, 'track_id': 0, 'x': 5.5, 'y': 5.5, 'state': None, 'score': 0.8},
])""",
        }

        result, standard, native, script_content = self.run_inline_tracking_adapter(
            "state_tracker", info, masks
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(standard["trackId"].tolist(), [1, 1])
        self.assertEqual(standard["state"].tolist(), ["G1", "none"])
        self.assertEqual(standard["continuous_label"].tolist(), [4, 7])
        self.assertEqual(native["track_id"].tolist(), [0, 0])
        self.assertIn("score", native.columns)
        self.assertIn("yaml.safe_dump", script_content)

    def test_inline_tracking_preserves_valid_lineage_with_custom_mapping(self):
        masks = np.zeros((2, 16, 16), dtype=np.uint16)
        masks[0, 4:7, 4:7] = 1
        masks[1, 2:5, 2:5] = 2
        masks[1, 8:11, 8:11] = 3
        info = {
            "name": "Lineage tracker",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "frame_base": 1,
            "column_map": {
                "time": "frame",
                "node": "trackId",
                "mask_id": "continuous_label",
                "cx": "Center_of_the_object_0",
                "cy": "Center_of_the_object_1",
                "phase": "state",
                "family": "lineageId",
                "parent": "parentTrackId",
            },
            "core_code": """final_track_df = pd.DataFrame([
    {'time': 1, 'node': 10, 'mask_id': 1, 'cx': 5, 'cy': 5, 'phase': 'mother', 'family': 10, 'parent': 0, 'confidence': 0.99},
    {'time': 2, 'node': 11, 'mask_id': 2, 'cx': 3, 'cy': 3, 'phase': 'daughter', 'family': 10, 'parent': 10, 'confidence': 0.95},
    {'time': 2, 'node': 12, 'mask_id': 3, 'cx': 9, 'cy': 9, 'phase': 'daughter', 'family': 10, 'parent': 10, 'confidence': 0.94},
])""",
        }

        result, standard, native, _ = self.run_inline_tracking_adapter(
            "lineage_tracker", info, masks
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(standard["frame"].tolist(), [0, 1, 1])
        self.assertEqual(standard["lineageId"].tolist(), [10, 10, 10])
        self.assertEqual(standard["parentTrackId"].tolist(), [0, 10, 10])
        self.assertEqual(standard["state"].tolist(), ["mother", "daughter", "daughter"])
        self.assertIn("confidence", native.columns)

    def test_inline_tracking_rejects_partial_or_invalid_lineage(self):
        masks = np.zeros((1, 12, 12), dtype=np.uint16)
        masks[0, 4:8, 4:8] = 1
        partial = {
            "name": "Partial lineage",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "core_code": """final_track_df = pd.DataFrame([
    {'frame': 0, 'track_id': 1, 'original_cell_id': 1, 'x': 5, 'y': 5, 'lineageId': 1},
])""",
        }
        result, standard, native, _ = self.run_inline_tracking_adapter(
            "partial_lineage", partial, masks
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(standard)
        self.assertIsNotNone(native)
        self.assertIn("lineageId and parentTrackId together", result.stderr)

        self_parent = {
            **partial,
            "name": "Self parent",
            "core_code": partial["core_code"].replace(
                "'lineageId': 1", "'lineageId': 1, 'parentTrackId': 1"
            ),
        }
        result, standard, _, _ = self.run_inline_tracking_adapter(
            "self_parent", self_parent, masks
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(standard)
        self.assertIn("self-parent", result.stderr)

    def test_inline_tracking_rejects_invalid_mask_assignments(self):
        masks = np.zeros((1, 12, 12), dtype=np.uint16)
        masks[0, 3:6, 3:6] = 1
        base = {
            "name": "Invalid mask assignment",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "core_code": """final_track_df = pd.DataFrame([
    {'frame': 0, 'track_id': 1, 'original_cell_id': 99, 'x': 4, 'y': 4},
])""",
        }
        result, standard, native, _ = self.run_inline_tracking_adapter(
            "invalid_mask", base, masks
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(standard)
        self.assertIsNotNone(native)
        self.assertIn("not present in mask.tif", result.stderr)

        duplicate = {
            **base,
            "name": "Duplicate mask assignment",
            "core_code": """final_track_df = pd.DataFrame([
    {'frame': 0, 'track_id': 1, 'original_cell_id': 1, 'x': 4, 'y': 4},
    {'frame': 0, 'track_id': 2, 'original_cell_id': 1, 'x': 4, 'y': 4},
])""",
        }
        result, standard, _, _ = self.run_inline_tracking_adapter(
            "duplicate_mask", duplicate, masks
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(standard)
        self.assertIn("one mask object more than once", result.stderr)

    def test_inline_tracking_rejects_missing_parents_and_cycles(self):
        masks = np.zeros((1, 16, 16), dtype=np.uint16)
        masks[0, 3:6, 3:6] = 1
        masks[0, 9:12, 9:12] = 2
        missing_parent = {
            "name": "Missing parent",
            "category": "tracking",
            "dependencies": [],
            "description": "test",
            "core_code": """final_track_df = pd.DataFrame([
    {'frame': 0, 'track_id': 1, 'original_cell_id': 1, 'x': 4, 'y': 4, 'lineageId': 1, 'parentTrackId': 9},
])""",
        }
        result, standard, native, _ = self.run_inline_tracking_adapter(
            "missing_parent", missing_parent, masks
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(standard)
        self.assertIsNotNone(native)
        self.assertIn("parent track that is absent", result.stderr)

        cycle = {
            **missing_parent,
            "name": "Lineage cycle",
            "core_code": """final_track_df = pd.DataFrame([
    {'frame': 0, 'track_id': 1, 'original_cell_id': 1, 'x': 4, 'y': 4, 'lineageId': 1, 'parentTrackId': 2},
    {'frame': 0, 'track_id': 2, 'original_cell_id': 2, 'x': 10, 'y': 10, 'lineageId': 1, 'parentTrackId': 1},
])""",
        }
        result, standard, _, _ = self.run_inline_tracking_adapter(
            "lineage_cycle", cycle, masks
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(standard)
        self.assertIn("lineage cycle", result.stderr)

    def test_zip_rejects_parent_path_and_symlink(self):
        parent_archive = io.BytesIO()
        with zipfile.ZipFile(parent_archive, "w") as archive:
            archive.writestr("../escape.py", "pass")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                store_widget._safe_extract_zip(parent_archive.getvalue(), temporary)

        symlink_archive = io.BytesIO()
        link = zipfile.ZipInfo("link.py")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(symlink_archive, "w") as archive:
            archive.writestr(link, "outside.py")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                store_widget._safe_extract_zip(symlink_archive.getvalue(), temporary)

    def test_package_entry_script_must_be_an_internal_python_file(self):
        valid_archive = io.BytesIO()
        with zipfile.ZipFile(valid_archive, "w") as archive:
            archive.writestr("adapter/main.py", "value = 1\n")
        valid_info = {
            "package_url": "https://example.org/adapter.zip",
            "sha256": "unused in patched test",
            "entry_script": "adapter/main.py",
        }
        with patch.object(store_widget, "_download_bytes", lambda *args: valid_archive.getvalue()), patch.object(
            store_widget, "_check_sha256", lambda *args: None
        ):
            temporary, stage, script_name = store_widget._prepare_package_stage(
                "package_adapter", valid_info
            )
            try:
                self.assertEqual(script_name, "package_adapter/adapter/main.py")
                self.assertTrue((stage / "adapter" / "main.py").is_file())
            finally:
                temporary.cleanup()

        invalid_archive = io.BytesIO()
        with zipfile.ZipFile(invalid_archive, "w") as archive:
            archive.writestr("adapter/main.py", "if")
        with patch.object(store_widget, "_download_bytes", lambda *args: invalid_archive.getvalue()), patch.object(
            store_widget, "_check_sha256", lambda *args: None
        ):
            with self.assertRaises(SyntaxError):
                store_widget._prepare_package_stage("package_adapter", valid_info)

        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary) / "outside.py"
            outside.write_text("value = 1\n", encoding="utf-8")
            traversal_info = {**valid_info, "entry_script": "../outside.py"}
            with patch.object(store_widget, "_download_bytes", lambda *args: valid_archive.getvalue()), patch.object(
                store_widget, "_check_sha256", lambda *args: None
            ):
                with self.assertRaises(FileNotFoundError):
                    store_widget._prepare_package_stage("package_adapter", traversal_info)

    def test_removal_choices_exclude_included_algorithms(self):
        registry = {
            "segmentation": {
                "cellpose_cyto": {"env": "included", "script": "included.py"},
                "extra": {
                    "env": "extra",
                    "script": "extra.py",
                    "source": "online_store",
                },
            },
            "tracking": {},
            "correction": {},
        }
        with patch.object(store_widget, "load_registry", lambda: registry):
            choices = store_widget.get_installed_algos()
        self.assertIn("extra", choices)
        self.assertNotIn("cellpose_cyto", choices)

    def test_store_cannot_replace_included_algorithm(self):
        info = {
            "name": "Collision",
            "category": "segmentation",
            "dependencies": [],
            "description": "test",
            "core_code": "final_mask = img",
            "id": "cellpose_cyto",
        }
        registry = {
            "segmentation": {
                "cellpose_cyto": {"env": "included", "script": "included.py"}
            },
            "tracking": {},
            "correction": {},
        }
        with patch.object(store_widget, "CLOUD_APP_STORE", {"cellpose_cyto": info}), patch.object(
            store_widget, "load_registry", lambda: json.loads(json.dumps(registry))
        ):
            with self.assertRaisesRegex(ValueError, "reserved"):
                store_widget.install_from_cloud("cellpose_cyto")

    def test_inline_adapter_is_committed_only_after_install_validation(self):
        info = store_widget._validate_algo_config(
            "future_tracker",
            {
                "name": "Future tracker",
                "category": "tracking",
                "dependencies": [],
                "description": "test",
                "core_code": "final_track_df = pd.DataFrame(columns=['frame', 'track_id', 'x', 'y'])",
            },
        )
        registry = {"segmentation": {}, "tracking": {}, "correction": {}}
        saved = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script_path = root / "step_tracking_future_tracker.py"
            common_patches = [
                patch.object(store_widget, "CLOUD_APP_STORE", {"future_tracker": info}),
                patch.object(store_widget, "USER_ALGO_DIR", root),
                patch.object(store_widget, "USER_DATA_DIR", root),
                patch.object(store_widget, "ensure_user_dirs", lambda: None),
                patch.object(store_widget, "load_registry", lambda: json.loads(json.dumps(registry))),
                patch.object(store_widget, "save_registry", lambda value: saved.append(value)),
                patch.object(store_widget, "get_env_cmd", lambda: "conda"),
                patch.object(store_widget, "_env_exists", lambda *args: True),
                patch.object(store_widget, "_install_framework_if_needed", lambda *args: []),
                patch.object(store_widget, "_refresh_store_widget_choices", lambda: None),
            ]
            with ExitStack() as stack:
                for patcher in common_patches:
                    stack.enter_context(patcher)
                stack.enter_context(
                    patch.object(store_widget, "_verify_cloud_imports", lambda *args: None)
                )
                store_widget.install_from_cloud("future_tracker")

            self.assertTrue(script_path.is_file())
            self.assertIn("native_track.csv", script_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved[-1]["tracking"]["future_tracker"]["source"],
                "online_store",
            )

            script_path.write_text("old_adapter = True\n", encoding="utf-8")
            saved.clear()
            with ExitStack() as stack:
                for patcher in common_patches:
                    stack.enter_context(patcher)
                stack.enter_context(
                    patch.object(
                        store_widget,
                        "_verify_cloud_imports",
                        side_effect=RuntimeError("validation failed"),
                    )
                )
                with self.assertRaisesRegex(RuntimeError, "validation failed"):
                    store_widget.install_from_cloud("future_tracker")

            self.assertEqual(script_path.read_text(encoding="utf-8"), "old_adapter = True\n")
            self.assertFalse(saved)

    def test_registered_adapter_is_discovered_by_pipeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "adapter.py"
            script.write_text("pass", encoding="utf-8")
            registry = {
                "segmentation": {
                    "future_model": {"env": "future", "script": str(script)}
                },
                "tracking": {},
                "correction": {},
            }
            with patch.object(widget, "load_registry", lambda: registry):
                self.assertIn("future_model", widget.get_algos("segmentation"))
                self.assertIn("future_model", widget.get_mask_reference_methods())


if __name__ == "__main__":
    unittest.main()
