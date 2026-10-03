"""Build clean BioTrack Studio source and complete release archives."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path


IGNORED_NAMES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ipynb_checkpoints",
    ".coverage",
    "build",
    "dist",
    "biotrack_studio.egg-info",
    "BioTrack_Studio_install_log.txt",
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak"}

FULL_ROOT_FILES = {
    "ACKNOWLEDGEMENTS.md",
    "environment.yml",
    "init_base_algos.py",
    "installer.py",
    "launch_biotrack_studio.py",
    "LICENSE",
    "pyproject.toml",
    "README.md",
    "registry.json",
    "setup_conda.bat",
    "setup_conda.sh",
    "start.sh",
    "THIRD_PARTY_NOTICES.md",
    "winstart.bat",
}
FULL_DIRECTORIES = {"biotrack_studio", "demo_data", "docs", "SC-Track"}


def ignored(path: Path) -> bool:
    return (
        any(
            part in IGNORED_NAMES
            or part.endswith(".egg-info")
            or part.startswith(".test_")
            for part in path.parts
        )
        or path.name.startswith("_protocol_page_")
        or path.suffix.lower() in IGNORED_SUFFIXES
    )


def copy_clean_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=lambda directory, names: {
            name for name in names if ignored(Path(directory, name).relative_to(source))
        },
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_tree(source: Path, archive: Path, top_level: str) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file() and not ignored(path.relative_to(source)):
                output.write(path, Path(top_level) / path.relative_to(source))


def validate_archive(path: Path, top_level: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = [Path(item.filename) for item in archive.infolist() if not item.is_dir()]
    if not names or any(name.parts[0] != top_level for name in names):
        raise RuntimeError(f"Archive does not use one '{top_level}' top-level folder: {path}")
    forbidden = [name for name in names if ignored(Path(*name.parts[1:]))]
    if forbidden:
        raise RuntimeError(f"Forbidden release files found in {path}: {forbidden[:5]}")


def build(source: Path, installer_dir: Path, output_dir: Path) -> None:
    source = source.resolve()
    installer_dir = installer_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    if source == output_dir or source in output_dir.parents:
        raise ValueError("Output directory must not be inside the source tree.")

    required_installers = {
        "Miniforge3-Linux-x86_64.sh",
        "Miniforge3-MacOSX-arm64.sh",
        "Miniforge3-MacOSX-x86_64.sh",
        "Miniforge3-Windows-x86_64.exe",
    }
    missing = sorted(name for name in required_installers if not (installer_dir / name).is_file())
    if missing:
        raise FileNotFoundError(f"Missing bundled installers: {missing}")

    output_dir.mkdir(parents=True)
    github_source = output_dir / "GitHub_Source"
    full_root = output_dir / "Full_Package" / "BioTrack_Studio"
    copy_clean_tree(source, github_source)
    full_root.mkdir(parents=True)

    for name in FULL_ROOT_FILES:
        shutil.copy2(github_source / name, full_root / name)
    for name in FULL_DIRECTORIES:
        copy_clean_tree(github_source / name, full_root / name)

    readme = (full_root / "README.md").read_text(encoding="utf-8")
    contributing_start = readme.index("## Contributing\n")
    license_start = readme.index("## License\n", contributing_start)
    readme = readme[:contributing_start] + readme[license_start:]
    (full_root / "README.md").write_text(readme, encoding="utf-8")

    bundled = full_root / "installers"
    bundled.mkdir()
    checksum_lines = ["SHA256 checksums for bundled Miniforge installers", ""]
    for name in sorted(required_installers):
        target = bundled / name
        shutil.copy2(installer_dir / name, target)
        checksum_lines.append(f"{sha256(target).upper()}  {name}")
    (full_root / "INSTALLER_CHECKSUMS.txt").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")

    full_zip = output_dir / "BioTrack_Studio_v1.0.0.zip"
    source_zip = output_dir / "BioTrack_Studio_v1.0.0_GitHub_Source.zip"
    zip_tree(full_root, full_zip, "BioTrack_Studio")
    zip_tree(github_source, source_zip, "GitHub_Source")
    validate_archive(full_zip, "BioTrack_Studio")
    validate_archive(source_zip, "GitHub_Source")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--installer-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.installer_dir, args.output_dir)


if __name__ == "__main__":
    main()
