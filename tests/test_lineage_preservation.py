import ast
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SOURCE_PATH = Path(__file__).resolve().parents[1] / "init_base_algos.py"


def embedded_script(variable_name):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Embedded script {variable_name} was not found")


def embedded_function(variable_name, function_name, namespace):
    tree = ast.parse(embedded_script(variable_name))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, f"<{variable_name}:{function_name}>", "exec"), namespace)
    return namespace[function_name]


class LineagePreservationTests(unittest.TestCase):
    def test_sctrack_normalization_retains_division_structure(self):
        convert = embedded_function("c_sc", "convert_sctrack_ids", {"pd": pd})
        native = pd.DataFrame(
            [
                {
                    "frame_index": 0,
                    "track_id": 7,
                    "cell_id": "7_0",
                    "parent_id": "7_0",
                    "center_y": 10,
                    "center_x": 10,
                },
                {
                    "frame_index": 1,
                    "track_id": 7,
                    "cell_id": "7_0",
                    "parent_id": "7_0",
                    "center_y": 11,
                    "center_x": 10,
                },
                {
                    "frame_index": 2,
                    "track_id": 7,
                    "cell_id": "7_1",
                    "parent_id": "7_0",
                    "center_y": 10,
                    "center_x": 9,
                },
                {
                    "frame_index": 2,
                    "track_id": 7,
                    "cell_id": "7_2",
                    "parent_id": "7_0",
                    "center_y": 12,
                    "center_x": 11,
                },
            ]
        )

        normalized = convert(native)
        mother = int(normalized.loc[normalized["cell_id"] == "7_0", "trackId"].iloc[0])
        daughters = normalized[normalized["cell_id"].isin(["7_1", "7_2"])]

        self.assertEqual(normalized["trackId"].nunique(), 3)
        self.assertEqual(normalized["lineageId"].nunique(), 1)
        self.assertEqual(set(daughters["parentTrackId"].astype(int)), {mother})

    def test_ultrack_graph_conversion_accepts_native_parent_forms(self):
        convert = embedded_function(
            "c_ult",
            "normalize_lineage_graph",
            {"np": np},
        )
        graph = {1: -1, 2: 1, 3: [1], 4: np.array([3])}

        self.assertEqual(
            convert(graph, 1, {2, 3, 4, 5}),
            {3: 2, 4: 2, 5: 4},
        )

    def test_ultrack_graph_rejects_invalid_relationships(self):
        convert = embedded_function(
            "c_ult",
            "normalize_lineage_graph",
            {"np": np},
        )
        with self.assertRaisesRegex(ValueError, "multiple parents"):
            convert({3: [1, 2]}, 0, {1, 2, 3})
        with self.assertRaisesRegex(ValueError, "missing track"):
            convert({3: 9}, 0, {1, 2, 3})
        with self.assertRaisesRegex(ValueError, "self-parent"):
            convert({3: 3}, 0, {1, 2, 3})

    def test_ultrack_lineage_roots_handle_nested_divisions_and_cycles(self):
        roots = embedded_function("c_ult", "lineage_roots", {})
        self.assertEqual(
            roots({1, 2, 3, 4, 5}, {2: 1, 3: 1, 4: 3, 5: 3}),
            {1: 1, 2: 1, 3: 1, 4: 1, 5: 1},
        )
        with self.assertRaisesRegex(ValueError, "cycle"):
            roots({1, 2}, {1: 2, 2: 1})

    def test_only_lineage_capable_trackers_export_lineage_columns(self):
        self.assertIn("lineageId", embedded_script("c_sc"))
        self.assertIn("parentTrackId", embedded_script("c_sc"))
        self.assertIn("lineageId", embedded_script("c_ult"))
        self.assertIn("parentTrackId", embedded_script("c_ult"))
        self.assertNotIn("lineageId", embedded_script("c_tp"))
        self.assertNotIn("parentTrackId", embedded_script("c_tp"))

    def test_ultrack_native_table_is_preserved_separately(self):
        script = embedded_script("c_ult")
        self.assertIn("tracking_output", script)
        self.assertIn("ultrack_native_track.csv", script)

    def test_correction_launchers_do_not_inject_or_refresh_lineage_graphs(self):
        for variable_name in ("c_h4tracks", "c_amdtrk"):
            script = embedded_script(variable_name)
            self.assertNotIn("lineage_graph_from_table", script)
            self.assertNotIn("refresh_lineage_graph", script)
            self.assertNotIn("refresh_with_lineage_graph", script)
            self.assertNotIn("graph=graph", script)


if __name__ == "__main__":
    unittest.main()
