"""Install the production StarDist requirements and process the bundled demo."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from installer import stardist_framework_packages  # noqa: E402


def main():
    output = Path(os.environ.get("RUNNER_TEMP", ROOT)) / "biotrack_stardist_validation"
    output.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.update(KERAS_HOME=str(output / "keras"), PYTHONNOUSERSITE="1")
    tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
    call = next(node.value for node in tree.body if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "install_algorithm" and ast.literal_eval(node.value.args[0]) == "stardist")
    dependencies = ast.literal_eval(call.args[2]).split()
    for requirements in [stardist_framework_packages("cpu"), dependencies]:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir"] + requirements,
                       env=environment, check=True)
    subprocess.run([sys.executable, "-m", "pip", "check"], env=environment, check=True)
    template = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "c_star" for target in node.targets))
    adapter = output / "step_segmentation_stardist.py"
    adapter.write_text(template, encoding="utf-8")
    import numpy as np
    import tifffile
    image = np.stack([tifffile.imread(path) for path in sorted((ROOT / "demo_data").glob("t*.tif"))])
    tifffile.imwrite(output / "image.tif", image, photometric="minisblack")
    subprocess.run([sys.executable, adapter, output / "image.tif", output], env=environment, check=True)
    mask = tifffile.imread(output / "mask.tif")
    assert mask.shape == image.shape and len(mask) == 20
    assert np.issubdtype(mask.dtype, np.integer) and mask.max() > 0
    record = {"passed": True, "python": sys.version, "framework": stardist_framework_packages("cpu"),
              "shape": list(mask.shape), "maximum_label": int(mask.max()),
              "labelled_objects": sum(len(np.unique(frame)) - int(0 in frame) for frame in mask)}
    (output / "result.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
