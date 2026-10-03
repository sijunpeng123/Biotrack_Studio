#!/usr/bin/env bash
set -u

cd "$(dirname "$0")" || exit 1
unset PYTHONPATH PYTHONHOME PYTHONEXECUTABLE __PYVENV_LAUNCHER__ VIRTUAL_ENV
export PYTHONNOUSERSITE=1

echo "BioTrack Studio Launcher"
echo "Folder: $(pwd)"
echo
echo "This window will show setup progress."
echo "If something fails, send BioTrack_Studio_install_log.txt to support."
echo

fail() {
  echo
  echo "ERROR: $1"
  echo
  exit 1
}

is_executable_file() {
  [[ -n "${1:-}" && -f "$1" && -x "$1" ]]
}

first_existing_executable() {
  local candidate
  for candidate in "$@"; do
    if is_executable_file "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

command_path() {
  local name="$1"
  type -P "$name" 2>/dev/null || true
}

find_conda() {
  local path_conda
  path_conda="$(command_path conda)"

  first_existing_executable \
    "${CONDA_EXE:-}" \
    "$path_conda" \
    "$HOME/miniconda3/bin/conda" \
    "$HOME/anaconda3/bin/conda" \
    "$HOME/mambaforge/bin/conda" \
    "$HOME/miniforge3/bin/conda" \
    "/software/miniconda3/bin/conda" \
    "/opt/miniconda3/bin/conda" \
    "/opt/anaconda3/bin/conda" \
    "/opt/mambaforge/bin/conda" \
    "/opt/miniforge3/bin/conda"
}

valid_python() {
  local candidate="$1"
  is_executable_file "$candidate" || return 1
  "$candidate" -c "import pathlib, subprocess, sys" >/dev/null 2>&1
}

find_python() {
  local conda_cmd="$1"
  local conda_bin conda_root path_python3 path_python candidate

  conda_bin="$(dirname "$conda_cmd")"
  conda_root="$(dirname "$conda_bin")"
  path_python3="$(command_path python3)"
  path_python="$(command_path python)"

  for candidate in \
    "${CONDA_PYTHON_EXE:-}" \
    "$conda_bin/python" \
    "$conda_root/bin/python" \
    "$path_python3" \
    "$path_python"
  do
    if valid_python "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

CONDA_CMD="$(find_conda || true)"
if [[ -z "$CONDA_CMD" ]]; then
  fail "Could not find conda. Install Miniconda or Anaconda first, then run this file again."
fi

if ! "$CONDA_CMD" --version >/dev/null 2>&1; then
  fail "Found conda at '$CONDA_CMD', but it cannot run."
fi

PYTHON_CMD="$(find_python "$CONDA_CMD" || true)"
if [[ -z "$PYTHON_CMD" ]]; then
  fail "Could not find Python to run the launcher. Fix the conda installation or install Python."
fi

# Use user-writable locations when the selected conda installation is shared
# or read-only. Users can override these before calling start.sh.
if [[ -z "${CONDA_ENVS_PATH:-}" ]]; then
  export CONDA_ENVS_PATH="$HOME/.conda/envs"
  mkdir -p "$CONDA_ENVS_PATH" 2>/dev/null || true
fi
if [[ -z "${CONDA_PKGS_DIRS:-}" ]]; then
  export CONDA_PKGS_DIRS="$HOME/.conda/pkgs"
  mkdir -p "$CONDA_PKGS_DIRS" 2>/dev/null || true
fi

export CONDA_EXE="$CONDA_CMD"

echo "Using conda: $CONDA_CMD"
echo "Using Python: $PYTHON_CMD"
echo "Conda envs path: ${CONDA_ENVS_PATH:-default}"
echo "Conda package cache: ${CONDA_PKGS_DIRS:-default}"
echo

"$PYTHON_CMD" "$(pwd)/launch_biotrack_studio.py"
