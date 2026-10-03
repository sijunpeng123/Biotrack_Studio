#!/usr/bin/env bash
set -u

cd "$(dirname "$0")" || exit 1
unset PYTHONPATH PYTHONHOME PYTHONEXECUTABLE __PYVENV_LAUNCHER__ VIRTUAL_ENV

echo "BioTrack Studio Conda Setup"
echo "Folder: $(pwd)"
echo

fail() {
  echo
  echo "ERROR: $1"
  echo
  exit 1
}

command_path() {
  type -P "$1" 2>/dev/null || true
}

find_existing_conda() {
  local candidate
  for candidate in \
    "${CONDA_EXE:-}" \
    "$(command_path conda)" \
    "$HOME/miniforge3/bin/conda" \
    "$HOME/miniconda3/bin/conda" \
    "$HOME/anaconda3/bin/conda" \
    "$HOME/mambaforge/bin/conda" \
    "/software/miniconda3/bin/conda" \
    "/opt/miniforge3/bin/conda" \
    "/opt/miniconda3/bin/conda" \
    "/opt/anaconda3/bin/conda"
  do
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" --version >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

initialize_shell_conda() {
  local conda_cmd="$1"
  local conda_bin
  local conda_root
  local shell_name

  conda_bin="$(dirname "$conda_cmd")"
  conda_root="$(dirname "$conda_bin")"
  shell_name="$(basename "${SHELL:-}")"
  if [[ "$shell_name" == "zsh" || "$shell_name" == "bash" ]]; then
    echo
    echo "Initializing conda for $shell_name:"
    echo "$conda_cmd init $shell_name"
    "$conda_cmd" init "$shell_name" || echo "WARNING: conda init $shell_name failed. You can still use bash start.sh."
  else
    echo
    echo "Skipping automatic shell initialization for shell: ${SHELL:-unknown}"
    echo "To use conda manually, run:"
    echo "source \"$conda_root/etc/profile.d/conda.sh\""
  fi
}

EXISTING_CONDA="$(find_existing_conda || true)"
if [[ -n "$EXISTING_CONDA" ]]; then
  echo "Conda is already available:"
  echo "$EXISTING_CONDA"
  initialize_shell_conda "$EXISTING_CONDA"
  echo
  echo "Run the BioTrack Studio launcher:"
  echo "bash start.sh"
  echo
  echo "If you want to use 'conda activate' manually, close and reopen Terminal first."
  exit 0
fi

PREFIX="${BIOTRACK_CONDA_PREFIX:-$HOME/miniforge3}"
if [[ -e "$PREFIX" && ! -x "$PREFIX/bin/conda" ]]; then
  fail "Install target already exists but is not a conda installation: $PREFIX
Remove that folder manually or set BIOTRACK_CONDA_PREFIX to another user-writable path."
fi

SYSTEM="$(uname -s)"
ARCH="$(uname -m)"
case "$SYSTEM:$ARCH" in
  Linux:x86_64) INSTALLER="Miniforge3-Linux-x86_64.sh" ;;
  Linux:aarch64) INSTALLER="Miniforge3-Linux-aarch64.sh" ;;
  Darwin:x86_64) INSTALLER="Miniforge3-MacOSX-x86_64.sh" ;;
  Darwin:arm64) INSTALLER="Miniforge3-MacOSX-arm64.sh" ;;
  *) fail "Unsupported platform for automatic Miniforge setup: $SYSTEM $ARCH" ;;
esac

URL="https://github.com/conda-forge/miniforge/releases/latest/download/$INSTALLER"
TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t biotrack-conda)"
TMP_INSTALLER="$TMP_DIR/$INSTALLER"
LOCAL_INSTALLER="$(pwd)/installers/$INSTALLER"
USING_BUNDLED=0

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ -f "$LOCAL_INSTALLER" ]]; then
  echo "Using bundled Miniforge installer:"
  echo "$LOCAL_INSTALLER"
  TMP_INSTALLER="$LOCAL_INSTALLER"
  USING_BUNDLED=1
else
  echo "Bundled Miniforge installer was not found:"
  echo "$LOCAL_INSTALLER"
  echo
  echo "Downloading Miniforge:"
  echo "$URL"
  echo

  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 3 -o "$TMP_INSTALLER" "$URL" || fail "Could not download Miniforge with curl.
For offline setup, copy $INSTALLER into the installers folder and rerun this script."
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$TMP_INSTALLER" "$URL" || fail "Could not download Miniforge with wget.
For offline setup, copy $INSTALLER into the installers folder and rerun this script."
  else
    fail "Neither curl nor wget is available.
For offline setup, copy $INSTALLER into the installers folder and rerun this script."
  fi
fi

if [[ "$USING_BUNDLED" -eq 1 && -f "$(pwd)/INSTALLER_CHECKSUMS.txt" ]]; then
  EXPECTED_HASH="$(awk -v name="$INSTALLER" '$2 == name {print tolower($1); exit}' "$(pwd)/INSTALLER_CHECKSUMS.txt")"
  if [[ -z "$EXPECTED_HASH" ]]; then
    fail "Installer checksum entry was not found for $INSTALLER."
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_HASH="$(sha256sum "$TMP_INSTALLER" | awk '{print tolower($1)}')"
  elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_HASH="$(shasum -a 256 "$TMP_INSTALLER" | awk '{print tolower($1)}')"
  else
    fail "Neither sha256sum nor shasum is available to verify the bundled installer."
  fi
  if [[ "$EXPECTED_HASH" != "$ACTUAL_HASH" ]]; then
    fail "Bundled Miniforge installer checksum failed for $INSTALLER."
  fi
  echo "Bundled installer checksum verified."
fi

echo
echo "Installing Miniforge to:"
echo "$PREFIX"
echo

bash "$TMP_INSTALLER" -b -p "$PREFIX" || fail "Miniforge installer failed."

if ! "$PREFIX/bin/conda" --version >/dev/null 2>&1; then
  fail "Miniforge installed, but conda could not run: $PREFIX/bin/conda"
fi

echo
echo "Conda setup finished:"
echo "$PREFIX/bin/conda"
initialize_shell_conda "$PREFIX/bin/conda"
echo
echo "Run the BioTrack Studio launcher:"
echo "bash start.sh"
echo
echo "If you want to use 'conda activate' manually, close and reopen Terminal first."
