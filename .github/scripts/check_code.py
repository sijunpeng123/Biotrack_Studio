"""Check first-party Python files and generated algorithm launchers."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    subprocess.run([sys.executable, "-m", "ruff", "check", "--no-cache", "."], cwd=ROOT, check=True)
    source_path = ROOT / "init_base_algos.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        source = node.value.value
        if not isinstance(source, str) or "\n" not in source:
            continue
        name = node.targets[0].id
        compile(source, name, "exec")
        subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--no-cache", "--stdin-filename", f"{name}.py", "-"],
            input=source,
            text=True,
            cwd=ROOT,
            check=True,
        )
    han_characters = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
    paths = list((ROOT / "biotrack_studio").glob("*.py")) + [source_path, ROOT / "installer.py", ROOT / "launch_biotrack_studio.py"]
    for path in paths:
        if han_characters.search(path.read_text(encoding="utf-8")):
            raise RuntimeError(f"Non-English source text requires review: {path.relative_to(ROOT)}")
    print("Python sources and generated launchers passed code checks.")


if __name__ == "__main__":
    main()
