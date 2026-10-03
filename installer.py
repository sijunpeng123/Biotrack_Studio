import os, subprocess, shutil, platform

os.environ.setdefault("PYTHONNOUSERSITE", "1")

from biotrack_studio.env_manager import get_env_cmd, get_subprocess_env
from biotrack_studio.paths import PROJECT_ROOT, USER_ALGO_DIR, ensure_user_dirs, load_registry, save_registry

def get_conda_cmd():
    return get_env_cmd()

def env_exists(env_name):
    conda_cmd = get_conda_cmd()
    try:
        result = subprocess.run(
            [conda_cmd, "env", "list"],
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

def get_hardware():
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mps"
    if shutil.which("nvidia-smi") is not None:
        try:
            subprocess.check_output(["nvidia-smi"], universal_newlines=True)
            return "cuda"
        except:
            pass
    return "cpu"

def _expected_modules(category, name):
    if category == "segmentation" and name in ("cellpose_cyto", "cellpose_nuclei"):
        return ["cellpose", "tifffile", "numpy"]
    if category == "segmentation" and name == "stardist":
        return ["stardist", "csbdeep", "tensorflow", "tifffile", "numpy"]
    if category == "tracking" and name == "trackpy":
        return ["trackpy", "skimage", "pandas", "yaml", "tifffile", "numpy"]
    if category == "tracking" and name == "sctrack":
        return ["SCTrack", "cv2", "pandas", "yaml", "tifffile", "numpy"]
    if category == "tracking" and name == "ultrack":
        return ["ultrack", "pandas", "yaml", "tifffile", "numpy"]
    return []

def _verify_imports(conda_cmd, env_name, modules):
    if not modules:
        return
    code = "import " + ", ".join(modules)
    print(f"[Build] Verifying imports in {env_name}: {', '.join(modules)}")
    subprocess.run([conda_cmd, "run", "-n", env_name, "python", "-c", code], check=True, env=get_subprocess_env())

def _imports_ready(conda_cmd, env_name, modules):
    if not modules:
        return True
    code = "import " + ", ".join(modules)
    try:
        subprocess.run(
            [conda_cmd, "run", "-n", env_name, "python", "-c", code],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=get_subprocess_env(),
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True

def _pip_install(conda_cmd, env_name, packages):
    subprocess.run(
        [
            conda_cmd,
            "run",
            "-n",
            env_name,
            "python",
            "-m",
            "pip",
            "install",
            "--retries",
            "10",
            "--timeout",
            "120",
        ] + packages,
        check=True,
        env=get_subprocess_env(),
    )


def _expected_correction_modules(plugin_name):
    if plugin_name == "MMV_H4Tracks":
        return ["napari", "mmv_h4tracks", "pandas", "tifffile"]
    if plugin_name == "napari-amdtrk":
        return ["napari", "napari_amdtrk", "pandas", "tifffile"]
    return []

def install_algorithm(algo_name, category, dependencies, script_content):
    env_name, script_name = f"env_{category}_{algo_name}", f"step_{category}_{algo_name}.py"
    print(f"\n[Build] Constructing isolated environment {env_name}...")
    
    conda_cmd = get_conda_cmd()
    
    expected_modules = _expected_modules(category, algo_name)
    environment_exists = env_exists(env_name)
    dependencies_ready = environment_exists and _imports_ready(
        conda_cmd, env_name, expected_modules
    )

    # Phase 1: Conda environment creation with fallback
    if environment_exists:
        print(f"[Build] Environment {env_name} already exists. Reusing it...")
    else:
        try:
            print("[Build] Attempting global conda environment creation...")
            subprocess.run([conda_cmd, "create", "-n", env_name, "python=3.10", "git", "-y"], check=True, env=get_subprocess_env())
        except subprocess.CalledProcessError:
            print("[Build] Global conda failed. Switching to mirror proxy...")
            tsinghua_conda = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/"
            subprocess.run([conda_cmd, "create", "-n", env_name, "python=3.10", "git", "-c", tsinghua_conda, "-y"], check=True, env=get_subprocess_env())
         
    deps_list = []
    for dep in dependencies.split():
        dep_path = PROJECT_ROOT / dep
        deps_list.append(str(dep_path) if dep_path.exists() else dep)

    if dependencies_ready:
        print(f"[Build] Dependencies in {env_name} are already available. Skipping pip installation.")
    else:
        hw_type = get_hardware()

        # Phase 2: Deep learning framework injection
        if "stardist" in algo_name and platform.system() == "Darwin":
            print(f"[Build] Injecting TensorFlow for Mac...")
            _pip_install(conda_cmd, env_name, ["tensorflow"])
        elif hw_type == "cuda":
            print(f"[Build] Injecting framework for GPU...")
            if "cellpose" in algo_name or "cellsam" in algo_name:
                _pip_install(conda_cmd, env_name, ["torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu118"])
            elif "stardist" in algo_name:
                _pip_install(conda_cmd, env_name, ["tensorflow[and-cuda]"])
        elif hw_type == "mps":
            print(f"[Build] Injecting framework for Mac...")
            if "cellpose" in algo_name or "cellsam" in algo_name:
                _pip_install(conda_cmd, env_name, ["torch", "torchvision"])
            elif "stardist" in algo_name:
                _pip_install(conda_cmd, env_name, ["tensorflow"])
        else:
            print(f"[Build] Falling back to CPU mode...")
            if "stardist" in algo_name:
                _pip_install(conda_cmd, env_name, ["tensorflow-cpu"])

        clean_deps = [d for d in deps_list if not d.startswith("tensorflow") and not d.startswith("torch")]

        # Phase 3: Algorithm dependencies installation with fallback
        if clean_deps:
            try:
                print(f"[Build] Attempting global pip installation...")
                _pip_install(conda_cmd, env_name, clean_deps)
            except subprocess.CalledProcessError:
                print(f"[Build] Global pip failed. Switching to mirror proxies...")

                mirror_deps = []
                for dep in clean_deps:
                    if "github.com" in dep:
                        mirror_deps.append(dep.replace("github.com", "mirror.ghproxy.com/https://github.com"))
                    else:
                        mirror_deps.append(dep)

                tsinghua_pip = "https://pypi.tuna.tsinghua.edu.cn/simple"
                _pip_install(conda_cmd, env_name, ["-i", tsinghua_pip] + mirror_deps)

        _verify_imports(conda_cmd, env_name, expected_modules)
             
    ensure_user_dirs()
    script_path = USER_ALGO_DIR / script_name
    with script_path.open("w", encoding="utf-8") as f:
        f.write(script_content.strip())
             
    reg = load_registry()
    reg.setdefault(category, {})
    reg[category][algo_name] = {"env": env_name, "script": script_name}
    save_registry(reg)
             
    print(f"[Success] {algo_name} deployed.\n")

def install_correction_plugin(
    plugin_name,
    dependencies,
    launcher_content,
    env_name=None,
    python_version="3.10",
    support_files=None,
):
    normalized_name = plugin_name.lower().replace("-", "_")
    env_name = env_name or f"env_correction_{normalized_name}"
    script_name = f"launch_correction_{normalized_name}.py"

    print(f"\n[Build] Constructing isolated correction environment {env_name}...")
    conda_cmd = get_conda_cmd()

    expected_modules = _expected_correction_modules(plugin_name)
    environment_exists = env_exists(env_name)
    dependencies_ready = environment_exists and _imports_ready(
        conda_cmd, env_name, expected_modules
    )

    if environment_exists:
        print(f"[Build] Environment {env_name} already exists. Reusing it...")
    else:
        try:
            print("[Build] Attempting global conda environment creation...")
            subprocess.run([conda_cmd, "create", "-n", env_name, f"python={python_version}", "-y"], check=True, env=get_subprocess_env())
        except subprocess.CalledProcessError:
            print("[Build] Global conda failed. Switching to mirror proxy...")
            tsinghua_conda = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/"
            subprocess.run([conda_cmd, "create", "-n", env_name, f"python={python_version}", "-c", tsinghua_conda, "-y"], check=True, env=get_subprocess_env())

    if dependencies_ready:
        print(f"[Build] Dependencies in {env_name} are already available. Skipping pip installation.")
    else:
        deps_list = dependencies.split()
        if deps_list:
            try:
                print("[Build] Attempting global pip installation...")
                _pip_install(conda_cmd, env_name, deps_list)
            except subprocess.CalledProcessError:
                print("[Build] Global pip failed. Switching to mirror proxy...")
                tsinghua_pip = "https://pypi.tuna.tsinghua.edu.cn/simple"
                _pip_install(conda_cmd, env_name, ["-i", tsinghua_pip] + deps_list)

        _verify_imports(conda_cmd, env_name, expected_modules)

    ensure_user_dirs()
    script_path = USER_ALGO_DIR / script_name
    with script_path.open("w", encoding="utf-8") as f:
        f.write(launcher_content.strip())
    for support_name, support_content in (support_files or {}).items():
        support_path = USER_ALGO_DIR / support_name
        with support_path.open("w", encoding="utf-8") as f:
            f.write(support_content.strip() + "\n")

    reg = load_registry()
    reg.setdefault("correction", {})
    reg["correction"][plugin_name] = {
        "env": env_name,
        "script": script_name,
        "mode": "external",
    }
    save_registry(reg)

    print(f"[Success] {plugin_name} correction environment deployed.\n")
