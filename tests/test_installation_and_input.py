import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import tifffile

from biotrack_studio._runtime_helpers import read_tiff
from biotrack_studio.utils import prepare_standard_tif


ROOT = Path(__file__).resolve().parents[1]


def load_source(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallationAndInputTests(unittest.TestCase):
    def test_existing_importable_environment_with_wrong_pin_is_rejected(self):
        installer = load_source("installer")
        code = installer._import_check_code(["json"], "pip==0.0.0")
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("AssertionError", process.stderr)
        self.assertIn("pip", process.stderr)

    def test_launcher_checks_requirements_for_all_eight_components(self):
        launcher = load_source("launch_biotrack_studio")
        requirements = launcher.bundled_requirements()
        self.assertEqual(len(requirements), 8)
        self.assertIn("pandas==2.2.3", requirements["env_correction_mmv_h4tracks"])
        self.assertIn("zarr==3.0.8", requirements["env_correction_mmv_h4tracks"])
        self.assertIn("napari-amdtrk==1.1.0", requirements["env_correction_napari_amdtrk"])

    def test_folder_with_one_multipage_tiff_preserves_time_and_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            image = np.arange(5 * 10 * 12, dtype=np.uint16).reshape(5, 10, 12)
            tifffile.imwrite(input_dir / "series.TIFF", image, photometric="minisblack")
            result = prepare_standard_tif(input_dir, output_dir)
            np.testing.assert_array_equal(tifffile.imread(result), image)

    def test_separate_frames_are_sorted_without_changing_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            for index in (2, 0, 1):
                tifffile.imwrite(input_dir / f"t{index:03}.tif", np.full((10, 12), index, np.uint16))
            result = tifffile.imread(prepare_standard_tif(input_dir, output_dir))
            self.assertEqual(result.shape, (3, 10, 12))
            self.assertEqual(result[:, 0, 0].tolist(), [0, 1, 2])

    def test_mixed_stacks_and_frames_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            tifffile.imwrite(input_dir / "stack.tif", np.zeros((5, 10, 12), np.uint8), photometric="minisblack")
            tifffile.imwrite(input_dir / "frame.tif", np.zeros((10, 12), np.uint8))
            with self.assertRaisesRegex(ValueError, "do not mix"):
                prepare_standard_tif(input_dir, output_dir)
            self.assertFalse((output_dir / "image.tif").exists())

    def test_rgb_input_is_rejected_instead_of_treating_channels_as_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "rgb.tif"
            tifffile.imwrite(image, np.zeros((10, 12, 3), np.uint8), photometric="rgb")
            with self.assertRaisesRegex(ValueError, "single-channel"):
                prepare_standard_tif(image, root)

    def test_compressed_tiffs_and_read_only_memmaps_preserve_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = np.arange(5 * 10 * 12, dtype=np.uint16).reshape(5, 10, 12)
            for compression in (None, "deflate"):
                path = root / f"{compression}.tif"
                tifffile.imwrite(path, image, compression=compression, photometric="minisblack")
                actual = read_tiff(path)
                np.testing.assert_array_equal(actual, image)
                if compression is None:
                    self.assertFalse(actual.flags.writeable)


if __name__ == "__main__":
    unittest.main()
