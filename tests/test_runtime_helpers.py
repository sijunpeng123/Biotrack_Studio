import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from biotrack_studio import _runtime_helpers


ROOT = Path(__file__).resolve().parents[1]


class RuntimeHelperTests(unittest.TestCase):
    def test_consensus_conda_uses_explicit_executable_and_home_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            explicit = home / "custom-conda"
            explicit.touch()
            fallback = home / "miniforge3" / "bin" / "conda"
            fallback.parent.mkdir(parents=True)
            fallback.touch()
            with patch.object(_runtime_helpers.Path, "home", return_value=home):
                with patch.dict(os.environ, {"CONDA_EXE": str(explicit)}, clear=True):
                    self.assertEqual(_runtime_helpers._find_conda_executable(), explicit)
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(_runtime_helpers._find_conda_executable(), fallback)
                fallback.unlink()
                with patch.dict(os.environ, {}, clear=True):
                    with patch.object(_runtime_helpers.shutil, "which", return_value=None):
                        with self.assertRaises(FileNotFoundError):
                            _runtime_helpers._find_conda_executable()

    def test_run_directories_preserve_existing_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs"
            with patch.object(_runtime_helpers.time, "strftime", return_value="fixed"):
                first = _runtime_helpers._unique_run_dir(root)
                evidence = first / "result.txt"
                evidence.write_text("keep", encoding="utf-8")
                second = _runtime_helpers._unique_run_dir(root)
            self.assertNotEqual(first, second)
            self.assertTrue(second.is_dir())
            self.assertEqual(evidence.read_text(encoding="utf-8"), "keep")

    def test_distributed_review_modules_import_without_biotrack_package(self):
        tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "CONSENSUS_SUPPORT_FILES" for target in node.targets)
        )
        namespace = {"_support_root": ROOT / "biotrack_studio"}
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), "<support files>", "exec"), namespace)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for name, source in namespace["CONSENSUS_SUPPORT_FILES"].items():
                (folder / name).write_text(source, encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(folder)
            result = subprocess.run(
                [sys.executable, "-c", "import pathlib, mask_consensus, consensus_review_panel, _runtime_helpers; assert pathlib.Path(_runtime_helpers.__file__).parent == pathlib.Path.cwd(); assert mask_consensus._unique_run_dir is _runtime_helpers._unique_run_dir"],
                cwd=folder,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_launcher_updates_correction_support_without_reinstalling(self):
        tree = ast.parse((ROOT / "launch_biotrack_studio.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "sync_correction_support_files")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "_runtime_helpers.py").write_text("old copy", encoding="utf-8")
            namespace = {
                "PROJECT_ROOT": ROOT,
                "USER_ALGO_DIR": target,
                "ensure_user_dirs": lambda: None,
                "shutil": __import__("shutil"),
                "log": lambda message: None,
            }
            exec(compile(ast.Module(body=[function], type_ignores=[]), "<support sync>", "exec"), namespace)
            namespace["sync_correction_support_files"]()
            for name in ("_runtime_helpers.py", "mask_consensus.py", "consensus_review_model.py", "consensus_review_panel.py"):
                self.assertEqual((target / name).read_bytes(), (ROOT / "biotrack_studio" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
