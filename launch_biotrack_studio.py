from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
import ast

from biotrack_studio.env_manager import get_subprocess_env, get_env_target_args
from biotrack_studio.paths import USER_ALGO_DIR, ensure_user_dirs


ENV_NAME = "biotrack_studio"
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_PATH = PROJECT_ROOT / "BioTrack_Studio_install_log.txt"


class LauncherError(Exception):
    pass


def log(message=""):
    text = str(message)
    print(text, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def reset_log():
    LOG_PATH.write_text(
        "BioTrack Studio launcher log\n"
        f"Started: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Folder: {PROJECT_ROOT}\n"
        f"Python: {sys.executable}\n"
        + "-" * 70
        + "\n",
        encoding="utf-8",
    )


def fail(message, step_name=None):
    log()
    if step_name:
        log(f"Failed at {step_name}.")
    log(f"ERROR: {message}")
    log()
    log("Setup did not finish. Your completed installation steps have been kept.")
    if os.name == "nt":
        log("Close this window, then run winstart.bat again to retry.")
    else:
        log("Close this window, then run bash start.sh again to retry.")
    log(f"Support log: {LOG_PATH}")
    log()
    log("Press Enter to close this window.")
    try:
        input()
    except EOFError:
        pass
    raise SystemExit(1)


def conda_download_message():
    return (
        "Could not find conda.\n"
        "Install Miniconda or Anaconda first, then run the launcher again.\n"
        "Miniconda download: https://docs.conda.io/en/latest/miniconda.html"
    )


def find_conda():
    candidates = []

    if os.environ.get("CONDA_EXE"):
        candidates.append(os.environ["CONDA_EXE"])

    found = shutil.which("conda")
    if found:
        candidates.append(found)

    home = Path.home()
    if os.name == "nt":
        candidates.extend(
            [
                home / "miniconda3" / "Scripts" / "conda.exe",
                home / "anaconda3" / "Scripts" / "conda.exe",
                home / "mambaforge" / "Scripts" / "conda.exe",
                home / "miniforge3" / "Scripts" / "conda.exe",
            ]
        )
        program_data = os.environ.get("ProgramData")
        if program_data:
            candidates.extend(
                [
                    Path(program_data) / "miniconda3" / "Scripts" / "conda.exe",
                    Path(program_data) / "anaconda3" / "Scripts" / "conda.exe",
                ]
            )
    else:
        candidates.extend(
            [
                home / "miniconda3" / "bin" / "conda",
                home / "anaconda3" / "bin" / "conda",
                home / "mambaforge" / "bin" / "conda",
                home / "miniforge3" / "bin" / "conda",
                Path("/software/miniconda3/bin/conda"),
                Path("/opt/miniconda3/bin/conda"),
                Path("/opt/anaconda3/bin/conda"),
                Path("/opt/mambaforge/bin/conda"),
                Path("/opt/miniforge3/bin/conda"),
            ]
        )

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)

    raise LauncherError(conda_download_message())


def child_process_env(conda=None):
    env = get_subprocess_env()
    if conda:
        env["CONDA_EXE"] = str(conda)
    return env


def run_stream(command, conda, use_env=False):
    full_command = [conda]
    if use_env:
        full_command += ["run", "--no-capture-output", *get_env_target_args(ENV_NAME)]
    full_command += [str(item) for item in command]

    log("Running:")
    log(" ".join(full_command))

    child_env = child_process_env(conda)

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            full_command,
            cwd=PROJECT_ROOT,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
        )
        if process.stdout:
            for line in process.stdout:
                print(line, end="", flush=True)
                log_file.write(line)
                log_file.flush()
        return_code = process.wait()

    if return_code != 0:
        raise LauncherError(f"Command failed with exit code {return_code}: {' '.join(full_command)}")


def run_capture(command):
    child_env = child_process_env(command[0] if command else None)

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        if result.stdout:
            log(result.stdout.rstrip())
        if result.stderr:
            log(result.stderr.rstrip())
        raise LauncherError(f"Command failed with exit code {result.returncode}: {' '.join(command)}")
    return result.stdout


def env_exists(conda):
    output = run_capture([conda, "env", "list", "--json"])
    envs = json.loads(output).get("envs", [])
    target = get_env_target_args(ENV_NAME)
    if target[0] == "-p":
        return any(Path(env_path).resolve() == Path(target[1]).resolve() for env_path in envs)
    return any(Path(env_path).name == ENV_NAME for env_path in envs)


def env_imports_ready(conda, env_name, modules, dependencies=""):
    from installer import _imports_ready

    return _imports_ready(conda, env_name, modules, dependencies)


def bundled_requirements():
    requirements = {}
    tree = ast.parse((PROJECT_ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name):
            continue
        if call.func.id == "install_algorithm":
            name, category, dependencies = [ast.literal_eval(arg) for arg in call.args[:3]]
            requirements[f"env_{category}_{name}"] = dependencies
        elif call.func.id == "install_correction_plugin":
            name, dependencies = [ast.literal_eval(arg) for arg in call.args[:2]]
            requirements[f"env_correction_{name.lower().replace('-', '_')}"] = dependencies
    return requirements


def algorithms_ready(conda):
    check_script = r'''
import ast
import json
from biotrack_studio.paths import PROJECT_ROOT, USER_ALGO_DIR, USER_REGISTRY_PATH

expected = {
    "segmentation": {
        "cellpose_cyto": ("step_segmentation_cellpose_cyto.py", "c_cyto"),
        "cellpose_nuclei": ("step_segmentation_cellpose_nuclei.py", "c_nuc"),
        "stardist": ("step_segmentation_stardist.py", "c_star"),
    },
    "tracking": {
        "trackpy": ("step_tracking_trackpy.py", "c_tp"),
        "sctrack": ("step_tracking_sctrack.py", "c_sc"),
        "ultrack": ("step_tracking_ultrack.py", "c_ult"),
    },
    "correction": {
        "MMV_H4Tracks": ("launch_correction_mmv_h4tracks.py", "c_h4tracks"),
        "napari-amdtrk": ("launch_correction_napari_amdtrk.py", "c_amdtrk"),
    },
}

try:
    registry = json.loads(USER_REGISTRY_PATH.read_text(encoding="utf-8"))
except Exception:
    print("NOT_READY")
    raise SystemExit(0)

try:
    init_tree = ast.parse((PROJECT_ROOT / "init_base_algos.py").read_text(encoding="utf-8"))
    templates = {}
    for node in init_tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("c_"):
                    try:
                        templates[target.id] = ast.literal_eval(node.value).strip()
                    except Exception:
                        pass
except Exception:
    print("NOT_READY")
    raise SystemExit(0)

for category, names in expected.items():
    for name, (script_name, template_name) in names.items():
        item = registry.get(category, {}).get(name)
        script_path = USER_ALGO_DIR / item.get("script", "") if item else None
        template = templates.get(template_name)
        if (
            not item
            or item.get("script") != script_name
            or not script_path
            or not script_path.exists()
            or not template
            or script_path.read_text(encoding="utf-8").strip() != template
        ):
            print("NOT_READY")
            raise SystemExit(0)

print("READY")
'''
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            suffix="_biotrack_check_algorithms.py",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(check_script)
            temp_path = Path(f.name)
        output = run_capture([conda, "run", *get_env_target_args(ENV_NAME), "python", str(temp_path)])
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    scripts_ready = any(line.strip() == "READY" for line in output.splitlines())
    if not scripts_ready:
        return False

    expected_imports = {
        "env_segmentation_cellpose_cyto": ["cellpose", "tifffile", "numpy"],
        "env_segmentation_cellpose_nuclei": ["cellpose", "tifffile", "numpy"],
        "env_segmentation_stardist": ["stardist", "csbdeep", "tensorflow", "tifffile", "numpy"],
        "env_tracking_trackpy": ["trackpy", "skimage", "pandas", "yaml", "tifffile"],
        "env_tracking_sctrack": ["SCTrack", "cv2", "pandas", "yaml", "tifffile"],
        "env_tracking_ultrack": ["ultrack", "pandas", "yaml", "tifffile"],
        "env_correction_mmv_h4tracks": ["napari", "mmv_h4tracks", "pandas", "tifffile"],
        "env_correction_napari_amdtrk": ["napari", "napari_amdtrk", "pandas", "tifffile"],
    }
    requirements = bundled_requirements()
    for env_name, modules in expected_imports.items():
        if not env_imports_ready(conda, env_name, modules, requirements[env_name]):
            log(f"Bundled algorithm environment is incomplete: {env_name}")
            return False

    return True


def sync_correction_support_files():
    ensure_user_dirs()
    source_dir = PROJECT_ROOT / "biotrack_studio"
    for name in (
        "_runtime_helpers.py",
        "consensus_review_model.py",
        "consensus_review_panel.py",
        "mask_consensus.py",
    ):
        source = source_dir / name
        target = USER_ALGO_DIR / name
        if source.is_file() and (
            not target.is_file()
            or target.read_bytes() != source.read_bytes()
        ):
            shutil.copy2(source, target)
            log(f"Updated correction support: {name}")


def step(number, title, action):
    step_name = f"Step {number}/5: {title}"
    log()
    log("=" * 70)
    log(step_name)
    log("=" * 70)
    try:
        result = action()
    except LauncherError as exc:
        fail(str(exc), step_name)
    except KeyboardInterrupt:
        fail("Launcher interrupted by user.", step_name)
    except Exception as exc:
        fail(f"Unexpected error: {exc}", step_name)
    log(f"{step_name} - Done.")
    return result


def main():
    reset_log()
    log("BioTrack Studio Launcher")
    log("This window will show each setup step. First setup can take a long time.")
    log(f"Log file: {LOG_PATH}")

    if not (PROJECT_ROOT / "biotrack_studio").is_dir() or not (PROJECT_ROOT / "environment.yml").is_file():
        fail("This launcher must stay inside the extracted BioTrack Studio folder.")

    conda_holder = {}

    def check_conda():
        conda = find_conda()
        conda_holder["conda"] = conda
        log(f"BioTrack Studio folder: {PROJECT_ROOT}")
        log(f"Conda: {conda}")

    def prepare_environment():
        conda = conda_holder["conda"]
        if env_exists(conda):
            log(f"Conda environment '{ENV_NAME}' already exists. Skipping environment creation.")
        else:
            run_stream(["env", "create", "-f", "environment.yml", *get_env_target_args(ENV_NAME)], conda)

    def install_plugin():
        run_stream(["python", "-m", "pip", "install", "-e", "."], conda_holder["conda"], use_env=True)

    def initialize_algorithms():
        conda = conda_holder["conda"]
        sync_correction_support_files()
        if algorithms_ready(conda):
            log("Bundled algorithms already initialized. Skipping algorithm initialization.")
        else:
            log("Initializing bundled algorithms. This can take a long time on first setup.")
            run_stream(["python", "init_base_algos.py"], conda, use_env=True)
            if not algorithms_ready(conda):
                raise LauncherError(
                    "Some bundled environments are incomplete. Check the failed method in this log "
                    "and run the launcher again to retry."
                )

    def open_napari():
        log("Starting napari.")
        log("When napari opens, use: Plugins > BioTrack Studio > Cell Analysis Pipeline")
        log("This launcher window will stay busy while napari is open.")
        run_stream(["napari"], conda_holder["conda"], use_env=True)

    step(1, "Checking conda", check_conda)
    step(2, "Creating or reusing BioTrack Studio environment", prepare_environment)
    step(3, "Installing BioTrack Studio", install_plugin)
    step(4, "Initializing bundled algorithms", initialize_algorithms)
    step(5, "Opening napari", open_napari)

    log()
    log("BioTrack Studio launcher finished.")
    log("Press Enter to close this window.")
    try:
        input()
    except EOFError:
        pass


if __name__ == "__main__":
    main()
