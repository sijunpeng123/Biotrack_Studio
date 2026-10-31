"""Install and run one bundled component in a fresh CI interpreter."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def definition(name):
    tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
    templates = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id.startswith("c_")}
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id not in {"install_algorithm", "install_correction_plugin"}:
            continue
        if ast.literal_eval(call.args[0]) != name:
            continue
        correction = call.func.id == "install_correction_plugin"
        dependencies = ast.literal_eval(call.args[1 if correction else 2])
        template_name = call.args[2 if correction else 3].id
        category = "correction" if correction else ast.literal_eval(call.args[1])
        return category, dependencies, templates[template_name]
    raise ValueError(f"Unknown bundled component: {name}")


def install(name, dependencies):
    packages = [str(ROOT / part) if (ROOT / part).exists() else part for part in dependencies.split()]
    if name == "stardist":
        packages += ["tensorflow" if platform.system() == "Darwin" else "tensorflow-cpu"]
    subprocess.run([sys.executable, "-m", "pip", "install", "--retries", "5", *packages], check=True)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)


def check(name, output):
    category, _, code = definition(name)
    output.mkdir(parents=True, exist_ok=True)
    if category == "correction":
        tool = "mmv" if name == "MMV_H4Tracks" else "amdtrk"
        subprocess.run([sys.executable, str(ROOT / ".github/scripts/check_correction.py"), tool,
                        "--output", str(output)], check=True)
        return
    import numpy as np
    import tifffile

    script = output / "adapter.py"
    script.write_text(code, encoding="utf-8")
    if category == "segmentation":
        frames = [tifffile.imread(path) for path in sorted((ROOT / "demo_data").glob("*.tif"))]
        if not frames:
            raise RuntimeError("Packaged demonstration frames are missing")
        image = np.stack(frames) if frames[0].ndim == 2 else frames[0]
        image_path = output / "image.tif"
        tifffile.imwrite(image_path, image, imagej=True, photometric="minisblack")
        command = [sys.executable, str(script), str(image_path), str(output)]
    else:
        mask = np.zeros((20, 128, 128), dtype=np.uint16)
        for frame in range(20):
            mask[frame, 20:35, 20 + frame:35 + frame] = 1
            mask[frame, 75:90, 75:90] = 2
        tifffile.imwrite(output / "mask.tif", mask, imagej=True, photometric="minisblack")
        tifffile.imwrite(output / "image.tif", mask.astype(np.uint8) * 100, imagej=True, photometric="minisblack")
        command = [sys.executable, str(script), str(output)]
    subprocess.run(command, cwd=output, check=True, timeout=900)
    mask = tifffile.imread(output / "mask.tif")
    assert mask.ndim == 3 and mask.shape[0] == 20 and np.issubdtype(mask.dtype, np.integer) and mask.max() > 0
    if category == "tracking":
        import pandas as pd

        rows = pd.read_csv(output / "track.csv")
        assert len(rows) > 0 and (output / "config.yaml").is_file()
        assert not rows.duplicated(["frame", "continuous_label"]).any()
        for row in rows.itertuples():
            assert int(row.continuous_label) in mask[int(row.frame)]
    print(json.dumps({"component": name, "frames": len(mask), "passed": True}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("component")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.install:
        install(args.component, definition(args.component)[1])
    if args.output:
        check(args.component, args.output.resolve())
    else:
        with tempfile.TemporaryDirectory() as temporary:
            check(args.component, Path(temporary))


if __name__ == "__main__":
    main()
