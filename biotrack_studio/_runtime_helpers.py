"""Shared runtime helpers for packaged and standalone review modules."""

import os
import shutil
import time
from pathlib import Path


def _find_conda_executable() -> Path:
    candidates = []
    for name in ("CONDA_EXE", "MAMBA_EXE"):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]))
    home = Path.home()
    candidates.extend(
        [
            home / "miniconda3" / "Scripts" / "conda.exe",
            home / "anaconda3" / "Scripts" / "conda.exe",
            home / "miniforge3" / "Scripts" / "conda.exe",
            home / "miniconda3" / "bin" / "conda",
            home / "miniforge3" / "bin" / "conda",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    located = shutil.which("conda") or shutil.which("mamba")
    if located:
        return Path(located)
    raise FileNotFoundError("Conda executable was not found")


def _unique_run_dir(root: Path) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    candidate = root / timestamp
    suffix = 1
    while candidate.exists():
        candidate = root / f"{timestamp}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate
