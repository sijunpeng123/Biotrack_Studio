import os
import platform
import shutil
from pathlib import Path


def get_env_cmd():
    env_conda = os.environ.get("CONDA_EXE")
    if env_conda and Path(env_conda).exists():
        return env_conda

    if platform.system() == "Windows":
        conda_exe = shutil.which("conda")
        if conda_exe:
            return conda_exe

        conda_bat = shutil.which("conda.bat")
        if conda_bat:
            return conda_bat

        home = Path.home()
        for root in ("miniconda3", "anaconda3", "mambaforge", "miniforge3"):
            for candidate in (
                home / root / "Scripts" / "conda.exe",
                home / root / "condabin" / "conda.bat",
            ):
                if candidate.exists():
                    return str(candidate)

    else:
        home = Path.home()
        for root in ("miniforge3", "miniconda3", "anaconda3", "mambaforge"):
            candidate = home / root / "bin" / "conda"
            if candidate.exists():
                return str(candidate)

        for candidate in (
            Path("/opt/miniforge3/bin/conda"),
            Path("/opt/miniconda3/bin/conda"),
            Path("/opt/anaconda3/bin/conda"),
            Path("/software/miniconda3/bin/conda"),
        ):
            if candidate.exists():
                return str(candidate)

    conda_cmd = shutil.which("conda")
    if conda_cmd:
        return conda_cmd

    for name in ("mamba", "micromamba"):
        cmd = shutil.which(name)
        if cmd:
            return cmd

    return "conda"


def get_subprocess_env():
    env = os.environ.copy()
    env.setdefault("PYTHONNOUSERSITE", "1")

    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONEXECUTABLE",
        "__PYVENV_LAUNCHER__",
        "VIRTUAL_ENV",
    ):
        env.pop(key, None)

    if platform.system() != "Windows":
        home = Path.home()
        env.setdefault("CONDA_ENVS_PATH", str(home / ".conda" / "envs"))
        env.setdefault("CONDA_PKGS_DIRS", str(home / ".conda" / "pkgs"))
        env.setdefault("CONDA_EXE", get_env_cmd())

        if platform.system() == "Darwin":
            for key in (
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            ):
                env.setdefault(key, "1")

    return env
