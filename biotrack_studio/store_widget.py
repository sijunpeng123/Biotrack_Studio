import hashlib, io, json, os, platform, queue, re, shutil, subprocess, tempfile, threading, time, zipfile
from pathlib import Path
import requests
from magicgui import magic_factory
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QProgressDialog
from .env_manager import get_env_cmd, get_subprocess_env
from .paths import PACKAGE_DIR, USER_ALGO_DIR, USER_DATA_DIR, ensure_user_dirs, load_registry, save_registry

os.environ.setdefault("PYTHONNOUSERSITE", "1")

CATALOG_URL_ENV = "BIOTRACK_CLOUD_CATALOG_URL"
DEFAULT_CATALOG_URL = "https://raw.githubusercontent.com/sijunpeng123/Biotrack_Studio/main/cloud_algos.json"
CLOUD_CATALOG_CACHE_PATH = USER_DATA_DIR / "cloud_catalog_cache.json"
CLOUD_APP_STORE = {}
STORE_WIDGETS = []
ALGORITHM_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
MAX_PACKAGE_FILES = 2000
MAX_PACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
TRACK_REQUIRED_COLUMNS = (
    "frame",
    "trackId",
    "state",
    "continuous_label",
    "Center_of_the_object_0",
    "Center_of_the_object_1",
)
TRACK_LINEAGE_COLUMNS = ("lineageId", "parentTrackId")
TRACK_COLUMN_ALIASES = {
    "frame": "frame",
    "track_id": "trackId",
    "original_cell_id": "continuous_label",
    "y": "Center_of_the_object_1",
    "x": "Center_of_the_object_0",
}

def _normalize_dependencies(dependencies):
    if dependencies is None:
        return []
    if isinstance(dependencies, str):
        return [dep for dep in dependencies.split() if dep]
    if isinstance(dependencies, list):
        return [str(dep) for dep in dependencies if str(dep).strip()]
    raise ValueError("dependencies must be a list or a space-separated string.")


def _dependency_name(requirement):
    return re.split(r"[<>=!~\[\s]", str(requirement).strip(), maxsplit=1)[0].lower()


def _with_inline_adapter_dependencies(category, dependencies):
    required = ["numpy", "tifffile"]
    if category == "tracking":
        required.extend(["pandas", "pyyaml"])
    existing = {_dependency_name(dependency) for dependency in dependencies}
    return list(dependencies) + [name for name in required if name not in existing]

def _validate_algo_config(algo_id, info):
    if not ALGORITHM_ID_PATTERN.fullmatch(str(algo_id)):
        raise ValueError(
            f"Catalog entry ID '{algo_id}' must contain only lowercase letters, "
            "numbers, dots, underscores, or hyphens."
        )
    if not isinstance(info, dict):
        raise ValueError(f"Catalog entry {algo_id} must be a JSON object.")
    required = ["name", "category", "dependencies", "description"]
    for key in required:
        if key not in info:
            raise ValueError(f"Catalog entry {algo_id} is missing '{key}'.")

    if info["category"] not in {"segmentation", "tracking"}:
        raise ValueError(f"Catalog entry {algo_id} has invalid category: {info['category']}")

    source_fields = ["core_code", "script_url", "package_url"]
    configured_sources = [key for key in source_fields if info.get(key)]
    if len(configured_sources) != 1:
        raise ValueError(
            f"Catalog entry {algo_id} must provide exactly one of "
            "core_code, script_url, or package_url."
        )

    if info.get("package_url"):
        if not info.get("entry_script"):
            raise ValueError(f"Catalog entry {algo_id} with package_url must provide entry_script.")
        if not info.get("sha256"):
            raise ValueError(f"Catalog entry {algo_id} with package_url must provide sha256.")
    if info.get("script_url") and not info.get("sha256"):
        raise ValueError(f"Catalog entry {algo_id} with script_url must provide sha256.")

    for key in ("script_url", "package_url"):
        if info.get(key) and not str(info[key]).startswith("https://"):
            raise ValueError(f"Catalog entry {algo_id} must use HTTPS for {key}.")

    frame_base = info.get("frame_base", 0)
    if isinstance(frame_base, bool) or frame_base not in {0, 1}:
        raise ValueError(f"Catalog entry {algo_id} frame_base must be 0 or 1.")

    column_map = info.get("column_map", {})
    if not isinstance(column_map, dict):
        raise ValueError(f"Catalog entry {algo_id} column_map must be a JSON object.")
    allowed_columns = set(TRACK_REQUIRED_COLUMNS + TRACK_LINEAGE_COLUMNS)
    for source, target in column_map.items():
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"Catalog entry {algo_id} column_map contains an invalid source column.")
        if target not in allowed_columns:
            raise ValueError(
                f"Catalog entry {algo_id} maps '{source}' to unsupported column '{target}'."
            )
    inline_tracking = info["category"] == "tracking" and bool(info.get("core_code"))
    if column_map and not inline_tracking:
        raise ValueError(
            f"Catalog entry {algo_id} column_map is valid only for inline tracking adapters."
        )
    if "frame_base" in info and not inline_tracking:
        raise ValueError(
            f"Catalog entry {algo_id} frame_base is valid only for inline tracking adapters."
        )

    normalized = dict(info)
    normalized["id"] = algo_id
    dependencies = _normalize_dependencies(info.get("dependencies"))
    if info.get("core_code"):
        dependencies = _with_inline_adapter_dependencies(info["category"], dependencies)
    normalized["dependencies"] = dependencies
    normalized["frame_base"] = frame_base
    normalized["column_map"] = dict(column_map)
    return normalized

def _read_catalog_from_path(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _read_catalog_from_url(url):
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.json()

def _get_remote_catalog_url():
    return os.environ.get(CATALOG_URL_ENV, "").strip() or DEFAULT_CATALOG_URL.strip()

def _read_cached_remote_catalog():
    if not CLOUD_CATALOG_CACHE_PATH.exists():
        return {}
    return _read_catalog_from_path(CLOUD_CATALOG_CACHE_PATH)

def _write_cached_remote_catalog(catalog):
    ensure_user_dirs()
    temp_path = CLOUD_CATALOG_CACHE_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=4)
    temp_path.replace(CLOUD_CATALOG_CACHE_PATH)

def _load_remote_catalog(use_cache=True):
    remote_url = _get_remote_catalog_url()
    if not remote_url:
        return {}

    try:
        remote_catalog = _read_catalog_from_url(remote_url)
        _write_cached_remote_catalog(remote_catalog)
        return remote_catalog
    except Exception:
        if use_cache:
            return _read_cached_remote_catalog()
        raise

def load_cloud_catalog(use_cache=True):
    catalog = {}
    local_catalog = PACKAGE_DIR / "cloud_algos.json"
    if local_catalog.exists():
        catalog.update(_read_catalog_from_path(local_catalog))

    catalog.update(_load_remote_catalog(use_cache=use_cache))

    normalized = {}
    for algo_id, info in catalog.items():
        normalized_id = str(algo_id).lower().replace(" ", "_")
        if normalized_id in normalized:
            raise ValueError(f"Duplicate normalized catalog ID: {normalized_id}")
        normalized[normalized_id] = _validate_algo_config(normalized_id, info)
    return normalized

def refresh_cloud_catalog():
    global CLOUD_APP_STORE
    CLOUD_APP_STORE = load_cloud_catalog(use_cache=False)
    _refresh_store_widget_choices()
    return len(CLOUD_APP_STORE)

def get_cloud_algos(*args):
    global CLOUD_APP_STORE
    try:
        CLOUD_APP_STORE = load_cloud_catalog()
    except Exception:
        CLOUD_APP_STORE = {}
    return ["--- Please Select ---"] + list(CLOUD_APP_STORE.keys())

def get_installed_algos(*args):
    base_options = ["--- Please Select ---"]
    try:
        registry = load_registry()
        installed = []
        for cat in ["segmentation", "tracking", "correction"]:
            installed.extend(
                name
                for name, info in registry.get(cat, {}).items()
                if info.get("source") == "online_store"
            )
        return base_options + installed if installed else base_options
    except: return base_options

def _refresh_store_widget_choices():
    for widget in list(STORE_WIDGETS):
        try:
            widget.cloud_algo.choices = get_cloud_algos()
        except Exception:
            try:
                widget.cloud_algo.reset_choices()
            except Exception:
                pass
        try:
            widget.delete_algo.choices = get_installed_algos()
        except Exception:
            try:
                widget.delete_algo.reset_choices()
            except Exception:
                pass

def _download_bytes(url, timeout=60):
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content

def _check_sha256(content, expected_sha256, algo_id):
    actual = hashlib.sha256(content).hexdigest()
    expected = str(expected_sha256).strip().lower()
    if actual.lower() != expected:
        raise ValueError(
            f"Checksum mismatch for {algo_id}. Expected {expected}, got {actual}."
        )

def _safe_extract_zip(content, target_dir):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > MAX_PACKAGE_FILES:
            raise ValueError("Algorithm package contains too many files.")
        if sum(member.file_size for member in members) > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise ValueError("Algorithm package is too large after extraction.")
        target_root = Path(target_dir).resolve()
        for member in members:
            member_path = Path(member.filename)
            unix_mode = member.external_attr >> 16
            is_symlink = (unix_mode & 0o170000) == 0o120000
            resolved_member = (target_root / member_path).resolve()
            if (
                member_path.is_absolute()
                or ".." in member_path.parts
                or is_symlink
                or target_root not in (resolved_member, *resolved_member.parents)
            ):
                raise ValueError(f"Unsafe path in algorithm package: {member.filename}")
        archive.extractall(target_dir)

def _prepare_package_stage(algo_id, info):
    content = _download_bytes(info["package_url"])
    _check_sha256(content, info["sha256"], algo_id)

    temp_dir = tempfile.TemporaryDirectory()
    stage_dir = Path(temp_dir.name) / algo_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    _safe_extract_zip(content, stage_dir)

    entry_script = str(info["entry_script"]).replace("\\", "/").strip("/")
    entry_path = (stage_dir / entry_script).resolve()
    stage_root = stage_dir.resolve()
    if (
        not entry_script
        or stage_root not in entry_path.parents
        or not entry_path.is_file()
    ):
        temp_dir.cleanup()
        raise FileNotFoundError(f"entry_script not found in package: {entry_script}")
    try:
        compile(entry_path.read_bytes(), str(entry_path), "exec")
    except Exception:
        temp_dir.cleanup()
        raise

    script_name = f"{algo_id}/{entry_script}"
    return temp_dir, stage_dir, script_name

def _commit_package_stage(algo_id, stage_dir):
    target_dir = USER_ALGO_DIR / algo_id
    backup_dir = USER_ALGO_DIR / f".{algo_id}_backup_{int(time.time())}"

    if target_dir.exists():
        target_dir.replace(backup_dir) if target_dir.is_file() else shutil.move(str(target_dir), str(backup_dir))
    try:
        shutil.copytree(stage_dir, target_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if backup_dir.exists():
            shutil.move(str(backup_dir), str(target_dir))
        raise


def _commit_adapter_script(script_path, script_content):
    compile(script_content, str(script_path), "exec")
    temporary_path = script_path.with_suffix(script_path.suffix + ".tmp")
    temporary_path.write_text(script_content.rstrip() + "\n", encoding="utf-8")
    temporary_path.replace(script_path)


def _build_inline_tracking_script(algo_id, info):
    column_map = dict(TRACK_COLUMN_ALIASES)
    column_map.update(info.get("column_map", {}))
    frame_base = int(info.get("frame_base", 0))
    core_code = str(info["core_code"]).rstrip()
    header = (
        "import sys, os, tifffile, yaml, pandas as pd, numpy as np\n"
        "output_dir = sys.argv[1]\n"
        "mask_path = os.path.join(output_dir, 'mask.tif')\n"
        "masks = tifffile.imread(mask_path)\n"
        "if masks.ndim == 2:\n"
        "    masks = masks[np.newaxis, ...]\n"
        "if masks.ndim != 3:\n"
        "    raise ValueError('Store tracking adapters require a 2D time-series mask.')\n"
    )
    normalization = f"""
if not isinstance(final_track_df, pd.DataFrame):
    raise TypeError("Store tracking core_code must create a pandas DataFrame named final_track_df.")

native_output_dir = os.path.join(output_dir, "tracking_output")
os.makedirs(native_output_dir, exist_ok=True)
final_track_df.to_csv(
    os.path.join(native_output_dir, {algo_id!r} + "_native_track.csv"),
    index=False,
)

rename_map = {column_map!r}
clean_df = final_track_df.rename(columns=rename_map, errors="ignore").copy()
if clean_df.columns.duplicated().any():
    duplicates = clean_df.columns[clean_df.columns.duplicated()].tolist()
    raise ValueError("Store tracking column_map creates duplicate columns: " + ", ".join(duplicates))

required = [
    "frame",
    "trackId",
    "Center_of_the_object_0",
    "Center_of_the_object_1",
]
missing = [column for column in required if column not in clean_df.columns]
if missing:
    raise ValueError("Store tracking output is missing required columns: " + ", ".join(missing))

for column in required:
    converted = pd.to_numeric(clean_df[column], errors="coerce")
    if converted.isna().any():
        raise ValueError("Store tracking output contains non-numeric values in " + column + ".")
    clean_df[column] = converted

clean_df["frame"] = clean_df["frame"] - {frame_base}
for column in ("frame", "trackId"):
    values = clean_df[column].to_numpy(dtype=float)
    if not np.allclose(values, np.rint(values)):
        raise ValueError("Store tracking output requires integer values in " + column + ".")
    clean_df[column] = np.rint(values).astype(int)

if (clean_df["frame"] < 0).any() or (clean_df["frame"] >= masks.shape[0]).any():
    raise ValueError("Store tracking output contains a frame outside mask.tif.")

lineage_present = [column in clean_df.columns for column in {TRACK_LINEAGE_COLUMNS!r}]
if any(lineage_present) and not all(lineage_present):
    raise ValueError("Store tracking output must provide lineageId and parentTrackId together.")
has_lineage = all(lineage_present)

if has_lineage:
    if (clean_df["trackId"] <= 0).any():
        raise ValueError("Lineage-aware Store adapters must provide positive trackId values.")
    for column in {TRACK_LINEAGE_COLUMNS!r}:
        converted = pd.to_numeric(clean_df[column], errors="coerce")
        if converted.isna().any():
            raise ValueError("Store tracking output contains non-numeric values in " + column + ".")
        values = converted.to_numpy(dtype=float)
        if not np.allclose(values, np.rint(values)):
            raise ValueError("Store tracking output requires integer values in " + column + ".")
        clean_df[column] = np.rint(values).astype(int)
    if (clean_df["lineageId"] <= 0).any() or (clean_df["parentTrackId"] < 0).any():
        raise ValueError("lineageId must be positive and root parentTrackId must be 0.")
else:
    if not clean_df.empty and clean_df["trackId"].min() <= 0:
        clean_df["trackId"] += int(1 - clean_df["trackId"].min())

if "state" in clean_df.columns:
    clean_df["state"] = clean_df["state"].fillna("none").astype(str).str.strip()
    clean_df.loc[clean_df["state"] == "", "state"] = "none"
else:
    clean_df["state"] = "none"

def labels_from_mask(table):
    labels = []
    max_y, max_x = masks.shape[-2] - 1, masks.shape[-1] - 1
    for _, row in table.iterrows():
        frame = int(row["frame"])
        y_value = float(row["Center_of_the_object_1"])
        x_value = float(row["Center_of_the_object_0"])
        if not (0 <= y_value <= max_y and 0 <= x_value <= max_x):
            raise ValueError("Store tracking output contains a coordinate outside mask.tif.")
        y = int(round(y_value))
        x = int(round(x_value))
        label = int(masks[frame, y, x])
        if label == 0:
            for radius in (3, 6, 12):
                y0, y1 = max(0, y - radius), min(max_y + 1, y + radius + 1)
                x0, x1 = max(0, x - radius), min(max_x + 1, x + radius + 1)
                coordinates = np.argwhere(masks[frame, y0:y1, x0:x1] > 0)
                if coordinates.size:
                    distances = (
                        (coordinates[:, 0] + y0 - y) ** 2
                        + (coordinates[:, 1] + x0 - x) ** 2
                    )
                    nearest = coordinates[int(np.argmin(distances))]
                    label = int(masks[frame, nearest[0] + y0, nearest[1] + x0])
                    break
        labels.append(label)
    return pd.Series(labels, index=table.index, dtype="int64")

if "continuous_label" in clean_df.columns:
    converted = pd.to_numeric(clean_df["continuous_label"], errors="coerce")
    if converted.isna().any():
        raise ValueError("Store tracking output contains non-numeric continuous_label values.")
    values = converted.to_numpy(dtype=float)
    if not np.allclose(values, np.rint(values)):
        raise ValueError("Store tracking output requires integer continuous_label values.")
    clean_df["continuous_label"] = np.rint(values).astype(int)
else:
    clean_df["continuous_label"] = labels_from_mask(clean_df)

mask_labels = [set(np.unique(frame).astype(int)) - {{0}} for frame in masks]
invalid_labels = [
    index
    for index, row in clean_df.iterrows()
    if int(row["continuous_label"]) <= 0
    or int(row["continuous_label"]) not in mask_labels[int(row["frame"])]
]
if invalid_labels:
    raise ValueError(
        "Store tracking output contains continuous_label values not present in mask.tif."
    )

if clean_df.duplicated(["frame", "trackId"]).any():
    raise ValueError("Store tracking output contains duplicate trackId values within a frame.")
if clean_df.duplicated(["frame", "continuous_label"]).any():
    raise ValueError("Store tracking output assigns one mask object more than once within a frame.")

if has_lineage and not clean_df.empty:
    for column in ("lineageId", "parentTrackId"):
        if (clean_df.groupby("trackId")[column].nunique() > 1).any():
            raise ValueError("Store tracking output changes " + column + " within one track.")
    relations = clean_df[["trackId", "lineageId", "parentTrackId"]].drop_duplicates("trackId")
    track_ids = set(relations["trackId"].astype(int))
    parent_by_track = dict(zip(relations["trackId"].astype(int), relations["parentTrackId"].astype(int)))
    lineage_by_track = dict(zip(relations["trackId"].astype(int), relations["lineageId"].astype(int)))
    for child, parent in parent_by_track.items():
        if parent == child:
            raise ValueError("Store tracking output contains a self-parent lineage relation.")
        if parent != 0 and parent not in track_ids:
            raise ValueError("Store tracking output references a parent track that is absent.")
        if parent != 0 and lineage_by_track[parent] != lineage_by_track[child]:
            raise ValueError("Parent and child tracks must have the same lineageId.")
        visited = set()
        current = child
        while current != 0:
            if current in visited:
                raise ValueError("Store tracking output contains a lineage cycle.")
            visited.add(current)
            current = parent_by_track.get(current, 0)

target_columns = {TRACK_REQUIRED_COLUMNS!r} + ({TRACK_LINEAGE_COLUMNS!r} if has_lineage else ())
clean_df[list(target_columns)].to_csv(os.path.join(output_dir, "track.csv"), index=False)
with open(os.path.join(output_dir, "config.yaml"), "w", encoding="utf-8") as stream:
    yaml.safe_dump(
        {{
            "intensity_suffix": "image",
            "mask_suffix": "mask",
            "track_suffix": "track",
            "frame_base": 0,
            "stateCol": "state",
        }},
        stream,
        sort_keys=False,
    )
"""
    return header + core_code + "\n" + normalization.lstrip()

def install_from_cloud(algo_id):
    if algo_id not in CLOUD_APP_STORE: raise ValueError(f"Algorithm {algo_id} not found.")
    info = CLOUD_APP_STORE[algo_id]
    category = info["category"]
    existing_registry = load_registry()
    existing = existing_registry.get(category, {}).get(algo_id)
    if existing and existing.get("source") != "online_store":
        raise ValueError(
            f"Algorithm ID '{algo_id}' is reserved by an included method and cannot be replaced."
        )
    env_name = info.get("env") or f"env_{category}_{algo_id}"
    script_name = f"step_{category}_{algo_id}.py"
    ensure_user_dirs()
    script_path = USER_ALGO_DIR / script_name
    package_temp = None
    package_stage = None

    if info.get("package_url"):
        package_temp, package_stage, script_name = _prepare_package_stage(algo_id, info)
    elif info.get("script_url"):
        content = _download_bytes(info["script_url"], timeout=30)
        _check_sha256(content, info["sha256"], algo_id)
        script_content = content.decode("utf-8")
    elif category == "segmentation":
        script_content = f"import sys, os, tifffile, numpy as np\nimg = tifffile.imread(sys.argv[1])\n{info['core_code']}\ntifffile.imwrite(os.path.join(sys.argv[2], 'mask.tif'), np.array(final_mask).astype(np.uint16), imagej=True)"
    else:
        script_content = _build_inline_tracking_script(algo_id, info)

    if not info.get("package_url"):
        compile(script_content, str(script_path), "exec")

    env_cmd = get_env_cmd()
    python_version = info.get("python_version", "3.10")
    dependencies = info["dependencies"]

    log_path = _make_store_log_path(algo_id)

    if not _env_exists(env_cmd, env_name):
        _run_install_command(
            [env_cmd, "create", "-n", env_name, f"python={python_version}", "git", "-y"],
            f"Creating environment for {algo_id}",
            log_path,
        )

    dependencies = _install_framework_if_needed(env_cmd, env_name, algo_id, dependencies, log_path)

    if dependencies:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as req:
            req.write("\n".join(dependencies))
            req_path = req.name
        try:
            _run_install_command(
                [env_cmd, "run", "-n", env_name, "python", "-m", "pip", "install", "-r", req_path],
                f"Installing dependencies for {algo_id}",
                log_path,
            )
        finally:
            try:
                os.remove(req_path)
            except OSError:
                pass
         
    try:
        _verify_cloud_imports(env_cmd, env_name, info, log_path)

        if package_stage is not None:
            _commit_package_stage(algo_id, package_stage)
        else:
            _commit_adapter_script(script_path, script_content)

        reg = load_registry()
        reg.setdefault(category, {})
        reg[category][algo_id] = {
            "env": env_name,
            "script": script_name,
            "source": "online_store",
        }
        if info.get("version"):
            reg[category][algo_id]["version"] = str(info["version"])
        save_registry(reg)
        _refresh_store_widget_choices()
        return log_path
    finally:
        if package_temp is not None:
            package_temp.cleanup()

def _install_framework_if_needed(env_cmd, env_name, algo_id, dependencies, log_path):
    normalized_deps = [dep.lower() for dep in dependencies]
    needs_cellpose_torch = "cellpose" in algo_id.lower() or any(dep.startswith("cellpose") for dep in normalized_deps)
    needs_stardist_tensorflow = (
        "stardist" in algo_id.lower()
        or any(dep.startswith("stardist") or dep.startswith("csbdeep") for dep in normalized_deps)
    )
    if not needs_cellpose_torch and not needs_stardist_tensorflow:
        return dependencies

    filtered = [
        dep for dep in dependencies
        if not dep.lower().startswith("torch")
        and not dep.lower().startswith("torchvision")
        and not dep.lower().startswith("tensorflow")
    ]

    if needs_stardist_tensorflow and _is_macos():
        _run_install_command(
            [
                env_cmd,
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "pip",
                "install",
                "tensorflow",
            ],
            f"Installing TensorFlow support for {algo_id}",
            log_path,
        )
    elif needs_stardist_tensorflow and _has_nvidia_gpu():
        _run_install_command(
            [
                env_cmd,
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "pip",
                "install",
                "tensorflow[and-cuda]",
            ],
            f"Installing GPU TensorFlow support for {algo_id}",
            log_path,
        )
    elif needs_stardist_tensorflow:
        _run_install_command(
            [
                env_cmd,
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "pip",
                "install",
                "tensorflow-cpu",
            ],
            f"Installing CPU TensorFlow support for {algo_id}",
            log_path,
        )

    if not needs_cellpose_torch:
        return filtered

    if _has_nvidia_gpu():
        _run_install_command(
            [
                env_cmd,
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "pip",
                "install",
                "torch==2.1.2",
                "torchvision==0.16.2",
                "--index-url",
                "https://download.pytorch.org/whl/cu118",
            ],
            f"Installing GPU support for {algo_id}",
            log_path,
        )
    else:
        _run_install_command(
            [
                env_cmd,
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "pip",
                "install",
                "torch==2.1.2",
                "torchvision==0.16.2",
            ],
            f"Installing CPU support for {algo_id}",
            log_path,
        )
    return filtered

def _is_macos():
    return platform.system() == "Darwin"

def _expected_cloud_modules(info):
    category = info.get("category")
    dependencies = [dep.lower() for dep in info.get("dependencies", [])]
    algo_id = str(info.get("id", "")).lower()

    if category == "segmentation" and (
        "cellpose" in algo_id or any(dep.startswith("cellpose") for dep in dependencies)
    ):
        return ["cellpose", "tifffile", "numpy"]
    if category == "segmentation" and (
        "stardist" in algo_id
        or any(dep.startswith("stardist") or dep.startswith("csbdeep") for dep in dependencies)
    ):
        return ["stardist", "csbdeep", "tensorflow", "tifffile", "numpy"]
    if category == "tracking" and any(dep.startswith("trackpy") for dep in dependencies):
        return ["trackpy", "skimage", "pandas", "yaml", "tifffile", "numpy"]
    return []

def _verify_cloud_imports(env_cmd, env_name, info, log_path):
    modules = _expected_cloud_modules(info)
    if not modules:
        return
    code = "import " + ", ".join(modules)
    _run_install_command(
        [env_cmd, "run", "-n", env_name, "python", "-c", code],
        f"Verifying imports for {info.get('id', env_name)}",
        log_path,
    )

def _has_nvidia_gpu():
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        subprocess.check_output(["nvidia-smi"], stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def _make_store_log_path(algo_id):
    logs_dir = USER_DATA_DIR / "logs" / "store"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", algo_id).strip("_") or "algorithm"
    return logs_dir / f"{timestamp}_{safe_id}.log"

def _run_install_command(cmd, title, log_path):
    output_queue = queue.Queue()
    recent_lines = []

    dialog = QProgressDialog(title, "Cancel", 0, 0)
    dialog.setWindowTitle("BioTrack Studio Algorithm Store")
    dialog.setWindowModality(Qt.NonModal)
    dialog.setMinimumDuration(0)
    dialog.setMinimumWidth(640)
    dialog.show()

    with open(log_path, "a", encoding="utf-8", errors="replace") as log_file:
        log_file.write("\n\n=== " + title + " ===\n")
        log_file.write("Command:\n")
        log_file.write(" ".join(str(part) for part in cmd) + "\n\n")
        log_file.flush()

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=get_subprocess_env(),
        )

        reader = threading.Thread(
            target=_read_process_output,
            args=(process, output_queue),
            daemon=True,
        )
        reader.start()

        start = time.time()
        while process.poll() is None:
            _drain_output(output_queue, log_file, recent_lines)
            elapsed = _format_elapsed(time.time() - start)
            dialog.setLabelText(
                _format_install_status(title, elapsed, recent_lines, log_path)
            )
            QApplication.processEvents()

            if dialog.wasCanceled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RuntimeError("Installation cancelled.")

            time.sleep(0.2)

        _drain_output(output_queue, log_file, recent_lines)
        dialog.close()

        if process.returncode != 0:
            raise RuntimeError(
                f"{title} failed. Please try again or contact the developer."
            )

def _read_process_output(process, output_queue):
    if process.stdout is None:
        return
    for line in process.stdout:
        output_queue.put(line.rstrip())

def _drain_output(output_queue, log_file, recent_lines):
    while True:
        try:
            line = output_queue.get_nowait()
        except queue.Empty:
            break
        log_file.write(line + "\n")
        log_file.flush()
        recent_lines.append(line)
        del recent_lines[:-25]

def _format_elapsed(seconds):
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"

def _format_install_status(title, elapsed, recent_lines, log_path):
    return (
        f"{title}\n"
        f"Elapsed: {elapsed}\n"
        "This may take several minutes. Please keep napari open."
    )

def _truncate_line(line, limit=150):
    line = str(line)
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."

def _env_exists(env_cmd, env_name):
    try:
        result = subprocess.run(
            [env_cmd, "env", "list"],
            check=False,
            capture_output=True,
            text=True,
            env=get_subprocess_env(),
        )
    except Exception:
        return False
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == env_name:
            return True
    return False

def _on_store_widget_init(widget):
    if widget not in STORE_WIDGETS:
        STORE_WIDGETS.append(widget)

    @widget.action_type.changed.connect
    def _toggle_visibility(action: str):
        is_install = action.startswith("2.")
        is_uninstall = action.startswith("3.")
        widget.cloud_algo.visible = is_install
        widget.delete_algo.visible = is_uninstall
    # Psygnal stores callbacks weakly; retain this nested callback with the widget.
    widget._biotrack_store_visibility_callback = _toggle_visibility
    _toggle_visibility(widget.action_type.value)

@magic_factory(
    widget_init=_on_store_widget_init, call_button="Execute Selected Action",
    action_type={"choices": ["1. Refresh Online Store", "2. Install Online Algorithm", "3. Remove Store Algorithm from Pipeline"], "widget_type": "RadioButtons", "label": "Select Action:"},
    cloud_algo={"choices": get_cloud_algos, "label": "[Online Store] Select Algorithm:"},
    delete_algo={"choices": get_installed_algos, "label": "[Remove] Select Store Algorithm:"}
)
def make_store_widget(action_type: str = "1. Refresh Online Store", cloud_algo: str = "--- Please Select ---", delete_algo: str = "--- Please Select ---"):
    from napari.utils.notifications import show_info, show_error
         
    if action_type.startswith("1."):
        try:
            count = refresh_cloud_catalog()
            show_info(f"Online Store refreshed. {count} algorithm(s) available.")
        except Exception as e:
            show_error(f"Refresh Error: {e}")

    elif action_type.startswith("2."):
        if cloud_algo in ["--- Please Select ---", "None"]: return show_info("Please select a valid algorithm.")
        try:
            install_from_cloud(cloud_algo)
            show_info(f"Installed {cloud_algo}")
        except Exception as e: show_error(f"Error: {e}")
             
    elif action_type.startswith("3."):
        if delete_algo in ["--- Please Select ---", "None"]: return show_info("Please select an algorithm.")
        try:
            reg = load_registry()
            for cat in ["segmentation", "tracking", "correction"]:
                if delete_algo in reg.get(cat, {}):
                    del reg[cat][delete_algo]
                    save_registry(reg)
                    return show_info(
                        f"Removed {delete_algo} from the Pipeline. Its environment and "
                        "downloaded files were kept. Reopen the Pipeline to refresh the list."
                    )
            show_error("Algorithm not found.")
        except Exception as e: show_error(f"Uninstall Error: {e}")
