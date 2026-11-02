"""Install the correction requirements embedded in the candidate."""
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
tree = ast.parse((ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
call = next(node.value for node in tree.body if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "install_correction_plugin"
            and ast.literal_eval(node.value.args[0]) == "MMV_H4Tracks")
if sys.platform == "linux":
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "torch==2.7.1",
                    "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir"] + ast.literal_eval(call.args[1]).split(),
               check=True)
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
