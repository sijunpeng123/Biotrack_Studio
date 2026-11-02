import ast
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer
import launch_biotrack_studio as launcher


ROOT = Path(__file__).resolve().parents[1]


class StarDistInstallationTests(unittest.TestCase):
    def test_framework_versions_for_supported_platforms(self):
        cases = [
            ("Windows", "AMD64", "cuda", "tensorflow-cpu==2.21.0", "ml-dtypes==0.5.4"),
            ("Linux", "x86_64", "cuda", "tensorflow[and-cuda]==2.21.0", "ml-dtypes==0.5.4"),
            ("Linux", "x86_64", "cpu", "tensorflow-cpu==2.21.0", "ml-dtypes==0.5.4"),
            ("Darwin", "arm64", "mps", "tensorflow==2.21.0", "ml-dtypes==0.5.4"),
            ("Darwin", "x86_64", "cpu", "tensorflow==2.16.2", "ml-dtypes==0.3.2"),
        ]
        for system, machine, hardware, framework, dtypes in cases:
            with self.subTest(system=system, machine=machine), patch.object(
                installer.platform, "system", return_value=system
            ), patch.object(installer.platform, "machine", return_value=machine):
                expected = [framework, "numpy==1.26.4", dtypes]
                if system == "Darwin" and machine == "x86_64":
                    expected.extend(["numba==0.62.1", "llvmlite==0.45.1"])
                self.assertEqual(installer.stardist_framework_packages(hardware), expected)

    def run_install(self, compatible, name="stardist", hardware="cuda"):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(installer, "get_conda_cmd", return_value="conda"))
            stack.enter_context(patch.object(installer, "get_hardware", return_value=hardware))
            stack.enter_context(patch.object(installer, "env_exists", return_value=True))
            stack.enter_context(patch.object(installer, "_imports_ready", return_value=True))
            stack.enter_context(patch.object(installer, "stardist_dependencies_compatible", return_value=compatible))
            stack.enter_context(patch.object(installer.platform, "system", return_value="Windows"))
            calls = stack.enter_context(patch.object(installer, "_pip_install"))
            processes = stack.enter_context(patch.object(installer.subprocess, "run"))
            stack.enter_context(patch.object(installer, "_verify_imports"))
            stack.enter_context(patch.object(installer, "ensure_user_dirs"))
            stack.enter_context(patch.object(installer, "USER_ALGO_DIR", Path(directory)))
            stack.enter_context(patch.object(installer, "load_registry", return_value={}))
            stack.enter_context(patch.object(installer, "save_registry"))
            installer.install_algorithm(name, "segmentation", "numpy==1.26.4", "print('adapter')")
            return calls.call_args_list, processes.call_args_list

    def test_importable_incompatible_framework_is_repaired(self):
        calls, processes = self.run_install(False)
        self.assertEqual(calls[0].args[2], ["tensorflow-cpu==2.21.0", "numpy==1.26.4", "ml-dtypes==0.5.4"])
        self.assertIn("uninstall", processes[0].args[0])
        self.assertIn("tensorflow-intel", processes[0].args[0])

    def test_compatible_existing_framework_is_kept(self):
        calls, processes = self.run_install(True)
        self.assertEqual(calls, [])
        self.assertEqual(processes, [])

    def test_launcher_retries_an_importable_incompatible_environment(self):
        with patch.object(launcher, "run_capture", return_value="READY\n"), patch.object(
            launcher, "env_imports_ready", return_value=True
        ), patch.object(installer, "stardist_dependencies_compatible", return_value=False), patch.object(
            installer, "mmv_dependencies_compatible", return_value=True
        ), patch.object(launcher, "log"):
            self.assertFalse(launcher.algorithms_ready("conda"))


class StarDistModelPathTests(unittest.TestCase):
    def test_relative_model_target_is_resolved_from_the_link_directory(self):
        tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
        template = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "c_star" for target in node.targets))
        function = next(node for node in ast.parse(template).body
                        if isinstance(node, ast.FunctionDef) and node.name == "safe_symlink")
        def deny_symlink(*args):
            raise OSError("symlink denied")

        namespace = {"Path": Path, "shutil": __import__("shutil"), "_original_symlink": deny_symlink}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "c_star", "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            weights = parent / "weights_extracted"
            weights.mkdir()
            (weights / "weights.bin").write_bytes(b"model data")
            for target in [Path("weights_extracted"), weights]:
                destination = parent / ("relative" if not target.is_absolute() else "absolute")
                namespace["safe_symlink"](destination, target)
                self.assertEqual((destination / "weights.bin").read_bytes(), b"model data")


if __name__ == "__main__":
    unittest.main()
