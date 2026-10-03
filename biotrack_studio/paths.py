import json
import os
import shutil
from pathlib import Path

try:
    from platformdirs import user_data_dir
except ImportError:
    user_data_dir = None


APP_NAME = "BioTrack Studio"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

BUNDLED_REGISTRY_PATHS = (
    PACKAGE_DIR / "registry.json",
    PROJECT_ROOT / "registry.json",
)


def _fallback_user_data_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(base) / APP_NAME
    if os.name == "posix" and os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


USER_DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False)) if user_data_dir else _fallback_user_data_dir()
USER_ALGO_DIR = USER_DATA_DIR / "algorithms"
USER_REGISTRY_PATH = USER_DATA_DIR / "registry.json"


def ensure_user_dirs():
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_ALGO_DIR.mkdir(parents=True, exist_ok=True)


def save_registry(registry):
    ensure_user_dirs()
    temp_path = USER_REGISTRY_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(registry, f, indent=4)
    temp_path.replace(USER_REGISTRY_PATH)


def load_registry():
    ensure_user_dirs()

    if not USER_REGISTRY_PATH.exists():
        for bundled_path in BUNDLED_REGISTRY_PATHS:
            if bundled_path.exists():
                shutil.copy2(bundled_path, USER_REGISTRY_PATH)
                break
        else:
            save_registry({"segmentation": {}, "tracking": {}, "correction": {}})

    with USER_REGISTRY_PATH.open("r", encoding="utf-8") as f:
        registry = json.load(f)

    registry.setdefault("segmentation", {})
    registry.setdefault("tracking", {})
    registry.setdefault("correction", {})
    return registry


def resolve_algorithm_script(script_name):
    script_path = Path(script_name)
    if script_path.is_absolute() and script_path.exists():
        return str(script_path)

    user_script = USER_ALGO_DIR / script_name
    if user_script.exists():
        return str(user_script)

    return str(PROJECT_ROOT / script_name)
