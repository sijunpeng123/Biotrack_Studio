"""Install exactly the MMV requirements declared by this candidate."""
import ast
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
tree = ast.parse((root / "init_base_algos.py").read_text(encoding="utf-8"))
call = next(node.value for node in tree.body if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "install_correction_plugin"
            and ast.literal_eval(node.value.args[0]) == "MMV_H4Tracks")
requirements = ast.literal_eval(call.args[1]).split()
subprocess.run([sys.executable, "-m", "pip", "install", *requirements], check=True)
subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
