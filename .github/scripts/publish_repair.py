"""Verify and replace the authorized v1.0.0 user installation package."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPOSITORY = "sijunpeng123/Biotrack_Studio"
TAG = "v1.0.0"
ASSET = "BioTrack_Studio_v1.0.0.zip"
OLD_DIGEST = "7a6615facee58b8aaa6e96697ec6e9d0b1c16aa8e22124cebd71266d6b710981"
OLD_TAG = "a08f453e3d0a1d449e3fc551c962ed99b0e525f7"


def command(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def api(endpoint):
    return json.loads(command("gh", "api", f"repos/{REPOSITORY}/{endpoint}"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ci-run", required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    root = Path(os.environ["RUNNER_TEMP"]) / "biotrack-repair"
    root.mkdir()
    assert command("git", "rev-parse", "HEAD", cwd=source) == args.commit
    assert api("git/ref/heads/main")["object"]["sha"] == args.commit
    assert api(f"git/ref/tags/{TAG}")["object"]["sha"] == OLD_TAG
    run = api(f"actions/runs/{args.ci_run}")
    assert run["head_sha"] == args.commit and run["conclusion"] == "success", run
    jobs = api(f"actions/runs/{args.ci_run}/jobs?per_page=100")["jobs"]
    assert len(jobs) == 27 and all(job["conclusion"] == "success" for job in jobs)

    before = api(f"releases/tags/{TAG}")
    assert len(before["assets"]) == 1 and before["assets"][0]["name"] == ASSET
    assert before["assets"][0]["digest"] == "sha256:" + OLD_DIGEST
    (root / "previous_release.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    previous = root / "previous"
    previous.mkdir()
    command("gh", "release", "download", TAG, "-R", REPOSITORY, "--pattern", ASSET, "--dir", str(previous))
    original = previous / ASSET
    assert digest(original) == OLD_DIGEST
    installers = root / "installers"
    installers.mkdir()
    with zipfile.ZipFile(original) as archive:
        for name in ("Miniforge3-Linux-x86_64.sh", "Miniforge3-MacOSX-arm64.sh", "Miniforge3-MacOSX-x86_64.sh", "Miniforge3-Windows-x86_64.exe"):
            (installers / name).write_bytes(archive.read(f"BioTrack_Studio/installers/{name}"))
    output = root / "release"
    subprocess.run([sys.executable, str(source / ".github/scripts/build_release.py"), "--source", str(source),
                    "--installer-dir", str(installers), "--output-dir", str(output)], check=True)
    package = output / "Full_Package/BioTrack_Studio"
    with zipfile.ZipFile(original) as archive:
        for name in archive.namelist():
            target = package / Path(name).relative_to("BioTrack_Studio")
            if not target.is_file():
                continue
            old, new = archive.read(name), target.read_bytes()
            if old != new and old.replace(b"\r\n", b"\n") == new.replace(b"\r\n", b"\n"):
                target.write_bytes(old)
    spec = importlib.util.spec_from_file_location("release_builder", source / ".github/scripts/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    archive_path = output / ASSET
    builder.zip_tree(package, archive_path, "BioTrack_Studio")
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        actual = [{"name": entry.filename, "size": entry.file_size,
                   "sha256": hashlib.sha256(archive.read(entry.filename)).hexdigest()} for entry in archive.infolist()]
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    assert sorted(actual, key=lambda item: item["name"]) == sorted(expected, key=lambda item: item["name"])
    assert not any(part in {"tests", ".github", ".git", "__pycache__", ".ruff_cache"}
                   for item in actual for part in Path(item["name"]).parts)

    subprocess.run(["gh", "release", "upload", TAG, "-R", REPOSITORY, str(archive_path), "--clobber"], check=True)
    after = api(f"releases/tags/{TAG}")
    assert len(after["assets"]) == 1 and after["assets"][0]["name"] == ASSET
    asset = after["assets"][0]
    assert asset["digest"] == "sha256:" + digest(archive_path)
    assert asset["size"] == archive_path.stat().st_size
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=source, check=True)
    subprocess.run(["git", "tag", "-a", "-f", TAG, args.commit, "-m", "BioTrack Studio v1.0.0 installation repair, 2026-11-01"], cwd=source, check=True)
    subprocess.run(["git", "push", f"--force-with-lease=refs/tags/{TAG}:{OLD_TAG}", "origin", f"refs/tags/{TAG}:refs/tags/{TAG}"], cwd=source, check=True)
    notes = root / "release_notes.md"
    notes.write_text(
        "BioTrack Studio v1.0.0 provides a cross-platform napari workflow for cell segmentation, tracking, consensus-assisted review, and manual correction.\n\n"
        "Download the complete user installation ZIP below and extract it before launching. It includes the user protocol, demonstration data, and platform-specific Miniforge installers.\n\n"
        "Maintenance repair (2026-11-01): included the manuscript's optional track-end and track-start checks; repaired MMV workspace initialization and correction dependency compatibility; consistently honored selected conda environment directories; and improved supported TIFF input handling. The six scientific adapter scripts, illustrated protocol, demonstration data, and production catalog were preserved.\n\n"
        "Validation: all 81 regression tests passed on each of Windows, macOS, and Linux. Fresh installation and functional checks passed for all eight bundled components on each platform (24 component checks). These are automated functional checks, including native correction save/reopen, rather than a new scientific benchmark.\n\n"
        f"Current source: https://github.com/{REPOSITORY}/commit/{args.commit}\n\n"
        f"Validation: {run['html_url']}\n", encoding="utf-8"
    )
    subprocess.run(["gh", "release", "edit", TAG, "-R", REPOSITORY, "--notes-file", str(notes)], check=True)
    report = {"source_commit": args.commit, "ci_run": run["html_url"], "all_27_ci_jobs_passed": True,
              "package_files_match_local_manifest": True, "package_files": len(actual), "asset": asset}
    (root / "publication_verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
