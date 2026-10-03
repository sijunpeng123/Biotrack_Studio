import os
import json
import time
import traceback
from pathlib import Path

os.environ.setdefault("PYTHONNOUSERSITE", "1")

from installer import install_algorithm as _install_algorithm, install_correction_plugin as _install_correction_plugin

_INIT_FAILURES = []
_INIT_LOG_PATH = os.environ.get("BIOTRACK_INIT_LOG", "").strip()


def _record_init_event(event):
    if not _INIT_LOG_PATH:
        return
    try:
        event = dict(event)
        event.setdefault("time", time.strftime("%Y-%m-%d %H:%M:%S"))
        path = Path(_INIT_LOG_PATH).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception:
        pass


def install_algorithm(algo_name, category, dependencies, script_content):
    _record_init_event(
        {
            "type": "algorithm",
            "name": algo_name,
            "category": category,
            "status": "START",
            "dependencies": dependencies,
        }
    )
    try:
        result = _install_algorithm(algo_name, category, dependencies, script_content)
        _record_init_event(
            {
                "type": "algorithm",
                "name": algo_name,
                "category": category,
                "status": "PASS",
            }
        )
        return result
    except Exception as exc:
        _INIT_FAILURES.append((f"{category}/{algo_name}", repr(exc), traceback.format_exc()))
        _record_init_event(
            {
                "type": "algorithm",
                "name": algo_name,
                "category": category,
                "status": "FAIL",
                "error": repr(exc),
            }
        )
        print(f"[Warning] Failed to initialize {category}/{algo_name}: {exc}")
        print("[Warning] Continuing with remaining bundled algorithms.")
        return None


def install_correction_plugin(
    plugin_name,
    dependencies,
    launcher_content,
    env_name=None,
    python_version="3.10",
    support_files=None,
):
    _record_init_event(
        {
            "type": "correction",
            "name": plugin_name,
            "category": "correction",
            "status": "START",
            "dependencies": dependencies,
        }
    )
    try:
        result = _install_correction_plugin(
            plugin_name,
            dependencies,
            launcher_content,
            env_name=env_name,
            python_version=python_version,
            support_files=support_files,
        )
        _record_init_event(
            {
                "type": "correction",
                "name": plugin_name,
                "category": "correction",
                "status": "PASS",
            }
        )
        return result
    except Exception as exc:
        _INIT_FAILURES.append((f"correction/{plugin_name}", repr(exc), traceback.format_exc()))
        _record_init_event(
            {
                "type": "correction",
                "name": plugin_name,
                "category": "correction",
                "status": "FAIL",
                "error": repr(exc),
            }
        )
        print(f"[Warning] Failed to initialize correction/{plugin_name}: {exc}")
        print("[Warning] Continuing with remaining bundled algorithms.")
        return None


print("============== Initializing Core Algorithms and Correction Tools ==============") 

c_cyto = '''import sys, os, tifffile, numpy as np 
img = tifffile.imread(sys.argv[1]) 
try:
    import torch
    use_gpu = bool(torch.cuda.is_available())
except Exception:
    use_gpu = False
try:     
    from cellpose import models     
    model = models.Cellpose(gpu=use_gpu, model_type='cyto') 
except AttributeError:     
    from cellpose.models import Cellpose     
    model = Cellpose(gpu=use_gpu, model_type='cyto') 
masks, _, _, _ = model.eval(list(img), diameter=None, channels=[0,0]) 
tifffile.imwrite(os.path.join(sys.argv[2], "mask.tif"), np.array(masks).astype(np.uint16), imagej=True)''' 
install_algorithm("cellpose_cyto", "segmentation", "cellpose==2.2.3 numpy==1.26.4 tifffile", c_cyto) 

c_nuc = '''import sys, os, tifffile, numpy as np 
img = tifffile.imread(sys.argv[1]) 
try:
    import torch
    use_gpu = bool(torch.cuda.is_available())
except Exception:
    use_gpu = False
try:     
    from cellpose import models     
    model = models.Cellpose(gpu=use_gpu, model_type='nuclei') 
except AttributeError:     
    from cellpose.models import Cellpose     
    model = Cellpose(gpu=use_gpu, model_type='nuclei') 
masks, _, _, _ = model.eval(list(img), diameter=None, channels=[0,0]) 
tifffile.imwrite(os.path.join(sys.argv[2], "mask.tif"), np.array(masks).astype(np.uint16), imagej=True)''' 
install_algorithm("cellpose_nuclei", "segmentation", "cellpose==2.2.3 numpy==1.26.4 tifffile", c_nuc) 

c_star = '''import sys, os, tifffile, numpy as np, shutil
from pathlib import Path

# --- Cross-Platform Symlink Patch for Windows ---
_original_symlink = Path.symlink_to
def safe_symlink(self, target, target_is_directory=False):
    try:
        _original_symlink(self, target, target_is_directory)
    except OSError: 
        if target.is_dir():
            shutil.copytree(target, self, dirs_exist_ok=True)
        else:
            shutil.copy2(target, self)
Path.symlink_to = safe_symlink
# ------------------------------------------------

from csbdeep.utils import normalize 
from stardist.models import StarDist2D 
img = tifffile.imread(sys.argv[1]) 
output_dir = sys.argv[2] 
model = StarDist2D.from_pretrained('2D_versatile_fluo') 
if img.ndim == 2:     
    img_norm = normalize(img, 1, 99.8, axis=(0,1))     
    masks, _ = model.predict_instances(img_norm) 
else:     
    masks = np.zeros_like(img, dtype=np.uint16)     
    for t in range(img.shape[0]):         
        img_norm = normalize(img[t], 1, 99.8, axis=(0,1))         
        labels, _ = model.predict_instances(img_norm)         
        masks[t] = labels 
tifffile.imwrite(os.path.join(output_dir, "mask.tif"), masks.astype(np.uint16), imagej=True)''' 
install_algorithm("stardist", "segmentation", "stardist==0.9.1 csbdeep==0.8.2 numpy==1.26.4 setuptools==80.9.0 tifffile", c_star) 

c_sc = '''import sys, os, shutil, yaml, pandas as pd, numpy as np, tifffile
output_dir = sys.argv[1] 

def labels_from_mask(mask_path, df):
    masks = tifffile.imread(mask_path)
    if masks.ndim == 2:
        masks = masks[np.newaxis, ...]

    labels = []
    max_t, max_y, max_x = masks.shape[0] - 1, masks.shape[-2] - 1, masks.shape[-1] - 1
    for _, row in df.iterrows():
        t = min(max(int(round(float(row["frame"]))), 0), max_t)
        y = min(max(int(round(float(row["Center_of_the_object_1"]))), 0), max_y)
        x = min(max(int(round(float(row["Center_of_the_object_0"]))), 0), max_x)
        label = int(masks[t, y, x])
        if label == 0:
            for radius in (3, 6, 12):
                y0, y1 = max(0, y - radius), min(max_y + 1, y + radius + 1)
                x0, x1 = max(0, x - radius), min(max_x + 1, x + radius + 1)
                coords = np.argwhere(masks[t, y0:y1, x0:x1] > 0)
                if coords.size:
                    distances = (coords[:, 0] + y0 - y) ** 2 + (coords[:, 1] + x0 - x) ** 2
                    nearest = coords[int(np.argmin(distances))]
                    label = int(masks[t, nearest[0] + y0, nearest[1] + x0])
                    break
        labels.append(label)
    return pd.Series(labels, index=df.index, dtype="int64")

def drop_duplicate_mask_labels(mask_path, df):
    duplicate_rows = df.duplicated(["frame", "continuous_label"], keep=False)
    if not duplicate_rows.any():
        return df

    masks = tifffile.imread(mask_path)
    if masks.ndim == 2:
        masks = masks[np.newaxis, ...]

    work = df.copy()
    work["_mask_distance"] = 0.0
    for (frame, label), group in work.loc[duplicate_rows].groupby(["frame", "continuous_label"], sort=False):
        try:
            t = int(frame)
            label_value = int(label)
        except Exception:
            work.loc[group.index, "_mask_distance"] = np.inf
            continue
        if t < 0 or t >= masks.shape[0] or label_value <= 0:
            work.loc[group.index, "_mask_distance"] = np.inf
            continue
        coords = np.argwhere(masks[t] == label_value)
        if coords.size == 0:
            work.loc[group.index, "_mask_distance"] = np.inf
            continue
        center_y, center_x = coords.mean(axis=0)
        dx = work.loc[group.index, "Center_of_the_object_0"].astype(float) - center_x
        dy = work.loc[group.index, "Center_of_the_object_1"].astype(float) - center_y
        work.loc[group.index, "_mask_distance"] = np.sqrt(dx * dx + dy * dy)

    work["_original_order"] = np.arange(len(work))
    before = len(work)
    work = work.sort_values(["frame", "continuous_label", "_mask_distance", "_original_order"], kind="mergesort")
    work = work.drop_duplicates(["frame", "continuous_label"], keep="first")
    work = work.sort_values("_original_order", kind="mergesort")
    removed = before - len(work)
    if removed:
        print(f"Tracking output: dropping {removed} duplicate mask-label rows for amdtrk-compatible track.csv.")
    return work.drop(columns=["_mask_distance", "_original_order"]).reset_index(drop=True)

def convert_sctrack_ids(track_df):
    # SC-Track's track_id identifies a whole lineage tree. cell_id identifies
    # an individual branch, which is the ID required by napari Tracks. Split
    # the occasional same-frame cell_id collision into continuous sub-branches.
    branch_source = track_df["cell_id"] if "cell_id" in track_df.columns else track_df["track_id"]
    branch_base = branch_source.astype("string").fillna(track_df["track_id"].astype("string"))
    branch_keys = branch_base.copy()

    for base_key in pd.unique(branch_base):
        indices = track_df.index[branch_base == base_key]
        group = track_df.loc[indices].sort_values(["frame_index", "center_y", "center_x"])
        if not group.duplicated("frame_index", keep=False).any():
            continue

        lanes = []
        assignments = {}
        for _, frame_rows in group.groupby("frame_index", sort=True):
            rows = list(frame_rows.iterrows())
            remaining_rows = set(range(len(rows)))
            remaining_lanes = set(range(len(lanes)))
            candidates = []
            for row_pos, (_, row) in enumerate(rows):
                for lane_pos in remaining_lanes:
                    lane = lanes[lane_pos]
                    distance = (float(row["center_y"]) - lane["y"]) ** 2 + (float(row["center_x"]) - lane["x"]) ** 2
                    candidates.append((distance, row_pos, lane_pos))
            for _, row_pos, lane_pos in sorted(candidates):
                if row_pos in remaining_rows and lane_pos in remaining_lanes:
                    assignments[rows[row_pos][0]] = lane_pos
                    remaining_rows.remove(row_pos)
                    remaining_lanes.remove(lane_pos)
            for row_pos in sorted(remaining_rows):
                lane_pos = len(lanes)
                lanes.append({"y": 0.0, "x": 0.0})
                assignments[rows[row_pos][0]] = lane_pos
            for row_index, row in rows:
                lane_pos = assignments[row_index]
                lanes[lane_pos] = {"y": float(row["center_y"]), "x": float(row["center_x"])}

        for row_index, lane_pos in assignments.items():
            branch_keys.loc[row_index] = f"{base_key}#{lane_pos}"

    unique_branches = pd.unique(branch_keys)
    branch_ids = {key: index + 1 for index, key in enumerate(unique_branches)}
    primary_branch_ids = {}
    for key, track_id in branch_ids.items():
        primary_branch_ids.setdefault(str(key).split("#", 1)[0], track_id)

    result = track_df.copy()
    result["trackId"] = branch_keys.map(branch_ids).astype(int)

    lineage_keys = branch_base.str.rsplit("_", n=1).str[0]
    lineage_roots = result.assign(_lineage_key=lineage_keys).groupby("_lineage_key")["trackId"].min()
    result["lineageId"] = lineage_keys.map(lineage_roots).astype(int)

    result["parentTrackId"] = 0
    if "parent_id" in result.columns:
        parent_keys = result["parent_id"].astype("string")
        mapped_parents = parent_keys.map(primary_branch_ids).fillna(0).astype(int)
        result["parentTrackId"] = mapped_parents.where(mapped_parents != result["trackId"], 0)
    return result
try:     
    from SCTrack.track import start_track     
    import SCTrack.refiner      
    def skip_visualization(*args, **kwargs): return np.zeros((10, 10), dtype=np.uint8)     
    SCTrack.refiner.generate_track_visualisation_from_df = skip_visualization     
    SCTRACK_AVAILABLE = True 
except ImportError:     
    SCTRACK_AVAILABLE = False 
if not SCTRACK_AVAILABLE:     
    print("SC-Track not found."); sys.exit(0) 
cwd = os.getcwd() 
try:     
    os.chdir(output_dir)     
    start_track(fannotation="mask.tif", fimage="image.tif", basename="image", fout=".", track_range=None) 
except Exception as e:     
    print(f"SC-Track Error: {e}") 
    sys.exit(1)
finally:     
    os.chdir(cwd) 
native_track_path = os.path.join(output_dir, "tracking_output", "track.csv") 
if os.path.exists(native_track_path):     
    track_df = pd.read_csv(native_track_path)     
    if track_df.empty:
        print("SC-Track Error: tracking_output/track.csv is empty.")
        sys.exit(1)
    if 'TrackID' in track_df.columns: track_df = track_df.rename(columns={'TrackID': 'track_id'})     
    required_native = ['frame_index', 'track_id', 'center_x', 'center_y']
    missing = [col for col in required_native if col not in track_df.columns]
    if missing:
        print("SC-Track Error: missing output columns: " + ", ".join(missing))
        sys.exit(1)
    clean_df = convert_sctrack_ids(track_df)
    clean_df = clean_df.rename(columns={'frame_index': 'frame', 'center_y': 'Center_of_the_object_1', 'center_x': 'Center_of_the_object_0'})
    clean_df['frame'] = pd.to_numeric(clean_df['frame'], errors='coerce')
    clean_df = clean_df.dropna(subset=['frame', 'trackId', 'Center_of_the_object_0', 'Center_of_the_object_1'])
    clean_df['frame'] = clean_df['frame'].astype(int)
    clean_df['state'] = clean_df['state'].fillna("none") if 'state' in clean_df.columns else "none"
    clean_df['continuous_label'] = labels_from_mask(os.path.join(output_dir, "mask.tif"), clean_df)
    gap_rows = clean_df['continuous_label'] <= 0
    if gap_rows.any():
        print(f"SC-Track: dropping {int(gap_rows.sum())} interpolated rows without a mask object.")
        clean_df = clean_df.loc[~gap_rows].copy()
    clean_df = drop_duplicate_mask_labels(os.path.join(output_dir, "mask.tif"), clean_df)
    duplicates = clean_df.duplicated(['trackId', 'frame'], keep=False)
    if duplicates.any():
        bad = clean_df.loc[duplicates, ['trackId', 'frame']].drop_duplicates().head(10).to_dict('records')
        print(f"SC-Track Error: branch IDs are not unique within a frame: {bad}")
        sys.exit(1)
    target_cols = ['frame', 'trackId', 'state', 'continuous_label', 'Center_of_the_object_0', 'Center_of_the_object_1', 'lineageId', 'parentTrackId']
    clean_df[target_cols].to_csv(os.path.join(output_dir, "track.csv"), index=False)     
    with open(os.path.join(output_dir, 'config.yaml'), 'w') as f:         
        yaml.dump({'intensity_suffix': 'image', 'mask_suffix': 'mask', 'track_suffix': 'track', 'frame_base': 0, 'stateCol': 'state'}, f)
else:
    print("SC-Track Error: tracking_output/track.csv was not generated.")
    sys.exit(1)''' 
# Note: Fixed pandas version to 2.2.3 for Python 3.10 compatibility
install_algorithm("sctrack", "tracking", "pandas==2.2.3 opencv-python-headless==4.11.0.86 pyyaml==6.0.3 numpy==2.2.6 tifffile ./SC-Track", c_sc) 

c_tp = '''import sys, os, tifffile, yaml, pandas as pd, numpy as np 
from skimage.measure import regionprops 
import trackpy as tp 
output_dir = sys.argv[1] 
masks = tifffile.imread(os.path.join(output_dir, "mask.tif")) 
if masks.ndim == 2: masks = masks[np.newaxis, ...] 
pts = [] 
for t in range(masks.shape[0]):     
    for p in regionprops(masks[t]):         
        pts.append({'frame': t, 'y': p.centroid[0], 'x': p.centroid[1], 'original_cell_id': p.label}) 
df = pd.DataFrame(pts) 
tracks = tp.link(df, search_range=15, memory=3, t_column='frame', adaptive_step=0.95) 
rename_map = {'frame': 'frame', 'particle': 'trackId', 'original_cell_id': 'continuous_label', 'y': 'Center_of_the_object_1', 'x': 'Center_of_the_object_0'} 
clean_df = tracks.rename(columns=rename_map) 
if 'trackId' in clean_df.columns and clean_df['trackId'].min() <= 0:
    clean_df['trackId'] = clean_df['trackId'].astype(int) + int(1 - clean_df['trackId'].min())
# State preservation fallback 
if 'state' in clean_df.columns: clean_df['state'] = clean_df['state'].fillna("none") 
else: clean_df['state'] = "none" 
if 'continuous_label' not in clean_df.columns: clean_df['continuous_label'] = clean_df['trackId'] 
target_cols = ['frame', 'trackId', 'state', 'continuous_label', 'Center_of_the_object_0', 'Center_of_the_object_1'] 
for col in target_cols:     
    if col not in clean_df.columns: clean_df[col] = 1 
clean_df[target_cols].to_csv(os.path.join(output_dir, "track.csv"), index=False) 
with open(os.path.join(output_dir, 'config.yaml'), 'w') as f:     
    yaml.dump({'intensity_suffix': 'image', 'mask_suffix': 'mask', 'track_suffix': 'track', 'frame_base': 0, 'stateCol': 'state'}, f)''' 
# Note: Fixed pandas version to 2.2.3 for Python 3.10 compatibility
install_algorithm("trackpy", "tracking", "trackpy==0.7 pandas==2.2.3 scikit-image==0.25.2 pyyaml tifffile numpy==1.26.4", c_tp) 

c_ult = '''import sys, os, tifffile, yaml, pandas as pd, numpy as np 
output_dir = sys.argv[1] 
masks = tifffile.imread(os.path.join(output_dir, "mask.tif")) 
if masks.ndim == 2: masks = masks[np.newaxis, ...] 

def labels_from_mask(df):
    labels = []
    max_t, max_y, max_x = masks.shape[0] - 1, masks.shape[-2] - 1, masks.shape[-1] - 1
    for _, row in df.iterrows():
        t = min(max(int(round(float(row["frame"]))), 0), max_t)
        y = min(max(int(round(float(row["Center_of_the_object_1"]))), 0), max_y)
        x = min(max(int(round(float(row["Center_of_the_object_0"]))), 0), max_x)
        label = int(masks[t, y, x])
        if label == 0:
            for radius in (3, 6, 12):
                y0, y1 = max(0, y - radius), min(max_y + 1, y + radius + 1)
                x0, x1 = max(0, x - radius), min(max_x + 1, x + radius + 1)
                coords = np.argwhere(masks[t, y0:y1, x0:x1] > 0)
                if coords.size:
                    distances = (coords[:, 0] + y0 - y) ** 2 + (coords[:, 1] + x0 - x) ** 2
                    nearest = coords[int(np.argmin(distances))]
                    label = int(masks[t, nearest[0] + y0, nearest[1] + x0])
                    break
        labels.append(label)
    return pd.Series(labels, index=df.index, dtype="int64")

def drop_duplicate_mask_labels(df):
    duplicate_rows = df.duplicated(["frame", "continuous_label"], keep=False)
    if not duplicate_rows.any():
        return df
    work = df.copy()
    work["_mask_distance"] = 0.0
    for (frame, label), group in work.loc[duplicate_rows].groupby(["frame", "continuous_label"], sort=False):
        try:
            t = int(frame)
            label_value = int(label)
        except Exception:
            work.loc[group.index, "_mask_distance"] = np.inf
            continue
        if t < 0 or t >= masks.shape[0] or label_value <= 0:
            work.loc[group.index, "_mask_distance"] = np.inf
            continue
        coords = np.argwhere(masks[t] == label_value)
        if coords.size == 0:
            work.loc[group.index, "_mask_distance"] = np.inf
            continue
        center_y, center_x = coords.mean(axis=0)
        dx = work.loc[group.index, "Center_of_the_object_0"].astype(float) - center_x
        dy = work.loc[group.index, "Center_of_the_object_1"].astype(float) - center_y
        work.loc[group.index, "_mask_distance"] = np.sqrt(dx * dx + dy * dy)
    work["_original_order"] = np.arange(len(work))
    before = len(work)
    work = work.sort_values(["frame", "continuous_label", "_mask_distance", "_original_order"], kind="mergesort")
    work = work.drop_duplicates(["frame", "continuous_label"], keep="first")
    work = work.sort_values("_original_order", kind="mergesort")
    removed = before - len(work)
    if removed:
        print(f"Ultrack: dropping {removed} duplicate mask-label rows for amdtrk-compatible track.csv.")
    return work.drop(columns=["_mask_distance", "_original_order"]).reset_index(drop=True)

try:     
    from ultrack import track, MainConfig, to_tracks_layer     
    from ultrack.utils import labels_to_contours 
except ImportError:     
    print("Ultrack not found."); sys.exit(0) 

def normalize_lineage_graph(graph, track_id_offset, valid_track_ids):
    normalized = {}
    for child, parent_value in graph.items():
        if isinstance(parent_value, (list, tuple, set, np.ndarray)):
            parents = [int(value) for value in parent_value if int(value) >= 0]
        else:
            parent = int(parent_value)
            parents = [parent] if parent >= 0 else []
        parents = list(dict.fromkeys(parents))
        if len(parents) > 1:
            raise ValueError(f"Ultrack lineage has multiple parents for track {child}: {parents}")
        if not parents:
            continue
        child_id = int(child) + track_id_offset
        parent_id = parents[0] + track_id_offset
        if child_id not in valid_track_ids or parent_id not in valid_track_ids:
            raise ValueError(f"Ultrack lineage references a missing track: {child_id} -> {parent_id}")
        if child_id == parent_id:
            raise ValueError(f"Ultrack lineage contains a self-parent relationship: {child_id}")
        normalized[child_id] = parent_id
    return normalized

def lineage_roots(track_ids, graph):
    roots = {}
    for track_id in track_ids:
        current = int(track_id)
        visited = set()
        while current in graph:
            if current in visited:
                raise ValueError(f"Ultrack lineage contains a cycle involving track {track_id}")
            visited.add(current)
            current = int(graph[current])
        roots[int(track_id)] = current
    return roots

cwd = os.getcwd() 
df = pd.DataFrame() 
native_df = pd.DataFrame()
lineage_graph = {}
try:     
    os.chdir(output_dir)     
    config = MainConfig()     
    foreground, contours = labels_to_contours(masks)     
    track(config=config, foreground=foreground, contours=contours)     
    tracks_data, lineage_graph = to_tracks_layer(config)     
    if tracks_data is not None and len(tracks_data) > 0:         
        if isinstance(tracks_data, pd.DataFrame):             
            native_df = tracks_data.copy()
            df = tracks_data.rename(columns={'t': 'frame'})         
        else:             
            if tracks_data.shape[1] == 4: native_df = pd.DataFrame(tracks_data, columns=['track_id', 't', 'y', 'x'])             
            elif tracks_data.shape[1] == 5: native_df = pd.DataFrame(tracks_data, columns=['track_id', 't', 'z', 'y', 'x'])
            df = native_df.rename(columns={'t': 'frame'})
except Exception as e:     
    print(f"Ultrack Error: {e}") 
finally:     
    os.chdir(cwd) 
if not df.empty:     
    native_output_dir = os.path.join(output_dir, "tracking_output")
    os.makedirs(native_output_dir, exist_ok=True)
    native_df.to_csv(os.path.join(native_output_dir, "ultrack_native_track.csv"), index=False)
    rename_map = {'frame': 'frame', 'track_id': 'trackId', 'y': 'Center_of_the_object_1', 'x': 'Center_of_the_object_0'}     
    clean_df = df.rename(columns=rename_map, errors='ignore')     
    track_id_offset = 0
    if 'trackId' in clean_df.columns and clean_df['trackId'].min() <= 0:
        track_id_offset = int(1 - clean_df['trackId'].min())
        clean_df['trackId'] = clean_df['trackId'].astype(int) + track_id_offset
    all_track_ids = set(clean_df['trackId'].astype(int).tolist())
    normalized_graph = normalize_lineage_graph(lineage_graph, track_id_offset, all_track_ids)
    # State preservation fallback     
    if 'state' in clean_df.columns: clean_df['state'] = clean_df['state'].fillna("none")     
    else: clean_df['state'] = "none"     
    target_cols = ['frame', 'trackId', 'state', 'continuous_label', 'Center_of_the_object_0', 'Center_of_the_object_1']     
    required_cols = ['frame', 'trackId', 'Center_of_the_object_0', 'Center_of_the_object_1']
    missing = [col for col in required_cols if col not in clean_df.columns]
    if missing:
        print("Ultrack Error: missing output columns: " + ", ".join(missing))
        sys.exit(1)
    clean_df = clean_df.dropna(subset=required_cols).copy()
    clean_df['frame'] = clean_df['frame'].astype(int)
    clean_df['continuous_label'] = labels_from_mask(clean_df)
    gap_rows = clean_df['continuous_label'] <= 0
    if gap_rows.any():
        print(f"Ultrack: dropping {int(gap_rows.sum())} rows without a mask object.")
        clean_df = clean_df.loc[~gap_rows].copy()
    clean_df = drop_duplicate_mask_labels(clean_df)
    active_track_ids = set(clean_df['trackId'].astype(int).tolist())
    active_graph = {
        child: parent for child, parent in normalized_graph.items()
        if child in active_track_ids and parent in active_track_ids
    }
    dropped_relationships = len(normalized_graph) - len(active_graph)
    if dropped_relationships:
        print(
            f"Ultrack: preserving {dropped_relationships} lineage relationships only in "
            "tracking_output/ultrack_native_track.csv because their tracks are not present "
            "in the mask-compatible standard table."
        )
    roots = lineage_roots(active_track_ids, active_graph)
    clean_df['lineageId'] = clean_df['trackId'].map(roots).astype(int)
    clean_df['parentTrackId'] = clean_df['trackId'].map(active_graph).fillna(0).astype(int)
    target_cols = [
        'frame', 'trackId', 'state', 'continuous_label',
        'Center_of_the_object_0', 'Center_of_the_object_1',
        'lineageId', 'parentTrackId',
    ]
    clean_df = clean_df[target_cols]     
    clean_df.to_csv(os.path.join(output_dir, "track.csv"), index=False)     
    with open(os.path.join(output_dir, 'config.yaml'), 'w') as f:         
        yaml.dump({'intensity_suffix': 'image', 'mask_suffix': 'mask', 'track_suffix': 'track', 'frame_base': 0, 'stateCol': 'state'}, f)''' 
# gevent 26.5.0 currently has no Windows cp310 wheel in our test path, so pip
# falls back to source builds that require Microsoft C++ Build Tools. Use a
# verified cp310 Windows wheel version and avoid "<" because conda.bat may
# treat it as shell redirection.
install_algorithm("ultrack", "tracking", "gevent==23.9.1 ultrack==0.6.3 pandas pyyaml tifffile numpy==1.26.4", c_ult) 

_support_root = Path(__file__).resolve().parent / "biotrack_studio"
CONSENSUS_SUPPORT_FILES = {
    "consensus_review_model.py": (_support_root / "consensus_review_model.py").read_text(encoding="utf-8"),
    "consensus_review_panel.py": (_support_root / "consensus_review_panel.py").read_text(encoding="utf-8"),
    "mask_consensus.py": (_support_root / "mask_consensus.py").read_text(encoding="utf-8"),
}

c_h4tracks = '''import sys, os, tifffile, pandas as pd, numpy as np, napari

def _snap_tracks_to_mask(tracks, mask):
    if mask is None:
        tracks["t"] = tracks["t"].round().astype(int)
        tracks["y"] = tracks["y"].round().astype(int)
        tracks["x"] = tracks["x"].round().astype(int)
        return tracks

    if mask.ndim == 2:
        mask = mask[np.newaxis, ...]

    snapped = []
    max_t, max_y, max_x = mask.shape[0] - 1, mask.shape[-2] - 1, mask.shape[-1] - 1

    for _, row in tracks.iterrows():
        t = int(np.rint(row["t"]))
        y = int(np.rint(row["y"]))
        x = int(np.rint(row["x"]))
        t = min(max(t, 0), max_t)

        y = min(max(int(y), 0), max_y)
        x = min(max(int(x), 0), max_x)
        snapped.append([int(row["track_id"]), t, y, x])

    return pd.DataFrame(snapped, columns=["track_id", "t", "y", "x"])

def add_tracks_layer(viewer, output_dir, mask=None):
    track_path = os.path.join(output_dir, "track.csv")
    if not os.path.exists(track_path):
        return

    df = pd.read_csv(track_path)
    col_map = {
        "trackId": "track_id",
        "track_id": "track_id",
        "frame": "t",
        "t": "t",
        "Center_of_the_object_1": "y",
        "y": "y",
        "Center_of_the_object_0": "x",
        "x": "x",
    }
    tracks = df.rename(columns=col_map)
    required_cols = ["track_id", "t", "y", "x"]
    if not all(col in tracks.columns for col in required_cols):
        print("track.csv cannot be converted to napari Tracks format.")
        return

    keep_cols = required_cols + (["label"] if "label" in tracks.columns else [])
    tracks = tracks[keep_cols].dropna(subset=required_cols).sort_values(["track_id", "t"])
    tracks = _snap_tracks_to_mask(tracks, mask)
    if not tracks.empty:
        viewer.add_tracks(tracks.to_numpy(), name="Tracks")

def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    image_path = os.path.join(output_dir, "image.tif")
    mask_path = os.path.join(output_dir, "mask.tif")

    viewer = napari.Viewer()
    if os.path.exists(image_path):
        viewer.add_image(tifffile.imread(image_path), name="Raw")
    mask = None
    if os.path.exists(mask_path):
        mask = tifffile.imread(mask_path)
        viewer.add_labels(mask, name="Mask")
    add_tracks_layer(viewer, output_dir, mask)

    correction_dock = None
    try:
        correction_dock, _ = viewer.window.add_plugin_dock_widget(
            plugin_name="mmv_h4tracks"
        )
    except Exception as e:
        print(f"Cannot open MMV_H4Tracks: {e}")
    try:
        from consensus_review_panel import attach_consensus_review
        attach_consensus_review(
            viewer,
            output_dir,
            "MMV_H4Tracks",
            correction_dock=correction_dock,
        )
    except Exception as e:
        print(f"Cannot open Consensus Review: {e}")

    napari.run()

if __name__ == "__main__":
    main()'''
install_correction_plugin(
    "MMV_H4Tracks",
    "napari[all] mmv_h4tracks==1.3.0 pandas tifffile imagecodecs",
    c_h4tracks,
    python_version="3.11",
    support_files=CONSENSUS_SUPPORT_FILES,
) 

c_amdtrk = '''import sys, os, napari
from napari_amdtrk._reader import reader_function
from napari_amdtrk._widget import AmdTrkWidget

def add_layer_from_reader(viewer, layer_tuple):
    data, metadata, layer_type = layer_tuple
    metadata = metadata or {}
    if layer_type == "image":
        viewer.add_image(data, **metadata)
    elif layer_type == "labels":
        viewer.add_labels(data, **metadata)
    elif layer_type == "tracks":
        viewer.add_tracks(data, **metadata)
    elif layer_type == "points":
        viewer.add_points(data, **metadata)
    else:
        raise ValueError(f"Unsupported amdtrk layer type: {layer_type}")

def check_standard_output(output_dir):
    required = ["image.tif", "mask.tif", "track.csv", "config.yaml"]
    missing = [name for name in required if not os.path.exists(os.path.join(output_dir, name))]
    if missing:
        raise FileNotFoundError("Missing standard BioTrack output files: " + ", ".join(missing))

def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    check_standard_output(output_dir)
    viewer = napari.Viewer()
    for layer_tuple in reader_function(output_dir):
        add_layer_from_reader(viewer, layer_tuple)

    correction_dock = None
    try:
        original_add_dock_widget = viewer.window.add_dock_widget
        created_dock = {}

        def add_amdtrk_dock(widget, *args, **kwargs):
            kwargs["name"] = "napari-amdtrk"
            kwargs["area"] = "right"
            dock = original_add_dock_widget(widget, *args, **kwargs)
            created_dock["dock"] = dock
            return dock

        viewer.window.add_dock_widget = add_amdtrk_dock
        try:
            amdtrk_widget = AmdTrkWidget(viewer)
        finally:
            viewer.window.add_dock_widget = original_add_dock_widget
        correction_dock = created_dock.get("dock")
    except Exception as e:
        print(f"Cannot open napari-amdtrk: {e}")
    try:
        from consensus_review_panel import attach_consensus_review
        attach_consensus_review(
            viewer,
            output_dir,
            "napari-amdtrk",
            correction_dock=correction_dock,
        )
    except Exception as e:
        print(f"Cannot open Consensus Review: {e}")

    napari.run()

if __name__ == "__main__":
    main()'''
install_correction_plugin(
    "napari-amdtrk",
    "napari[all]==0.5.6 napari-amdtrk==1.1.0 numpy==1.23.5 pandas==2.0.3 scikit-image==0.24.0 scipy==1.15.3 trackpy==0.7 tifffile imagecodecs pyyaml",
    c_amdtrk,
    python_version="3.10",
    support_files=CONSENSUS_SUPPORT_FILES,
)

if _INIT_FAILURES:
    print("============== Bundled Algorithm Initialization Finished With Warnings ==============")
    for name, error, detail in _INIT_FAILURES:
        print(f"[Failed] {name}: {error}")
        print(detail[-2000:])
else:
    print("============== Core Algorithms and Correction Tools Initialization Complete ==============")
