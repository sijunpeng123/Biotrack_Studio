"""Publish exactly the author-approved archives after merged-main checks pass."""
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "publication_config.json").read_text())
REPO = CONFIG["repository"]
BASE = "https://api.github.com/repos/" + REPO
INPUT = Path(os.environ["RUNNER_TEMP"]) / "biotrack_approved_archives"
RECORDS = Path(os.environ["RUNNER_TEMP"]) / "biotrack_publication_records"
RECORDS.mkdir()
TOKEN = os.environ["GH_TOKEN"]
STATE = {"approved_source": CONFIG["approved_source"], "main_commit": CONFIG["main_commit"],
         "author_approval": CONFIG["author_approval"], "phase": "preflight", "complete": False}


def save():
    (RECORDS / "publication_verification.json").write_text(json.dumps(STATE, indent=2), encoding="utf-8")


def api(method, path, payload=None, binary=None):
    url = path if path.startswith("https://") else BASE + "/" + path
    assert urllib.parse.urlsplit(url).hostname in {"api.github.com", "uploads.github.com"}
    headers = {"Authorization": "Bearer " + TOKEN, "User-Agent": "BioTrack-Approved-Publisher",
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if binary is not None:
        data = binary.read_bytes()
        headers["Content-Type"] = "application/zip" if binary.suffix == ".zip" else "text/plain"
    request = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        message = json.loads(error.read()).get("message", "GitHub request failed")
        raise RuntimeError(f"{method} {urllib.parse.urlsplit(url).path}: HTTP {error.code}: {message}") from None


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def public_download(url, target, expected, attempts=4):
    assert urllib.parse.urlsplit(url).hostname == "github.com"
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": "BioTrack-Approved-Publisher",
                                                      "Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=180) as response, target.open("wb") as stream:
            while block := response.read(1024 * 1024):
                stream.write(block)
        if digest(target) == expected:
            return
        if attempt + 1 < attempts:
            time.sleep(10)
    raise RuntimeError("Public download checksum mismatch: " + target.name)


def verify_asset(asset, expected):
    assert asset["state"] == "uploaded"
    assert asset["size"] == expected["bytes"]
    assert asset["digest"] == "sha256:" + expected["sha256"]


def wait_for_main_ci():
    expected_jobs = {"tests": 3, "Installation compatibility": 7}
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        listing = api("GET", "actions/runs?head_sha=" + CONFIG["main_commit"] + "&per_page=30")
        runs = {}
        for run in listing["workflow_runs"]:
            if run["event"] == "push" and run["head_branch"] == "main" and run["name"] in expected_jobs:
                runs.setdefault(run["name"], run)
        print("MAIN_CI", {name: (run["status"], run["conclusion"]) for name, run in runs.items()}, flush=True)
        if len(runs) == 2 and all(run["status"] == "completed" for run in runs.values()):
            assert all(run["conclusion"] == "success" for run in runs.values()), "Merged-main CI failed"
            evidence = []
            for name, run in runs.items():
                jobs = api("GET", f"actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
                assert len(jobs) == expected_jobs[name] and all(job["conclusion"] == "success" for job in jobs)
                evidence.append({"name": name, "run_id": run["id"], "url": run["html_url"],
                                 "jobs": [{"id": job["id"], "name": job["name"], "conclusion": job["conclusion"]} for job in jobs]})
            return evidence
        time.sleep(15)
    raise TimeoutError("Merged-main checks did not finish")


def main():
    assert CONFIG["author_approval"] == "批准合并 main、同步标签并替换正式安装包"
    for name, expected in CONFIG["archives"].items():
        path = INPUT / name
        assert path.stat().st_size == expected["bytes"] and digest(path) == expected["sha256"]
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
    assert api("GET", "git/ref/heads/main")["object"]["sha"] == CONFIG["main_commit"]
    commit = api("GET", "git/commits/" + CONFIG["main_commit"])
    assert commit["tree"]["sha"] == CONFIG["approved_tree"]
    STATE["main_ci"] = wait_for_main_ci()
    before = api("GET", "releases/tags/" + CONFIG["tag"])
    assert before["id"] == CONFIG["release_id"] and not before["draft"] and not before.get("immutable", False)
    assert len(before["assets"]) == 1
    original = before["assets"][0]
    assert original["id"] == CONFIG["old_asset_id"] and original["name"] == "BioTrack_Studio_v1.0.0.zip"
    assert original["digest"] == "sha256:" + CONFIG["old_asset_sha256"]
    old_tag = api("GET", "git/ref/tags/" + CONFIG["tag"])
    assert old_tag["object"]["sha"] == CONFIG["old_tag_object"]
    (RECORDS / "previous_release.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    (RECORDS / "previous_tag.json").write_text(json.dumps(old_tag, indent=2), encoding="utf-8")
    public_download(original["browser_download_url"], RECORDS / "previous_installation_package.zip",
                    CONFIG["old_asset_sha256"])
    STATE.update(phase="uploading_verified_archives", previous_release_id=before["id"],
                 previous_tag=old_tag["object"]["sha"], local_archives_verified=True)
    save()
    staged = {}
    for name, expected in CONFIG["archives"].items():
        temporary = name.removesuffix(".zip") + ".upload-" + expected["sha256"][:12] + ".zip"
        upload_url = before["upload_url"].split("{", 1)[0] + "?name=" + urllib.parse.quote(temporary)
        asset = api("POST", upload_url, binary=INPUT / name)
        verify_asset(asset, expected)
        public_download(asset["browser_download_url"], RECORDS / temporary, expected["sha256"])
        staged[name] = asset
        STATE["staged_assets"] = {key: {"id": value["id"], "name": value["name"], "digest": value["digest"]} for key, value in staged.items()}
        save()
    assert api("GET", "git/ref/heads/main")["object"]["sha"] == CONFIG["main_commit"]
    assert api("GET", "git/ref/tags/" + CONFIG["tag"])["object"]["sha"] == CONFIG["old_tag_object"]
    new_tag = api("POST", "git/tags", {"tag": CONFIG["tag"], "message": "BioTrack Studio v1.0.0: author-approved reviewed maintenance release; source " + CONFIG["approved_source"],
                                       "object": CONFIG["main_commit"], "type": "commit",
                                       "tagger": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"}})
    api("PATCH", "git/refs/tags/" + CONFIG["tag"], {"sha": new_tag["sha"], "force": True})
    STATE.update(phase="synchronizing_canonical_assets", tag_object=new_tag["sha"])
    save()
    backup_name = "BioTrack_Studio_v1.0.0.previous-20261023.zip"
    api("PATCH", f"releases/assets/{original['id']}", {"name": backup_name})
    for name, asset in staged.items():
        renamed = api("PATCH", f"releases/assets/{asset['id']}", {"name": name})
        verify_asset(renamed, CONFIG["archives"][name])
    checksum_path = INPUT / "SHA256SUMS.txt"
    expected_checksums = "".join(f"{item['sha256']}  {name}\n" for name, item in CONFIG["archives"].items())
    assert checksum_path.read_text(encoding="ascii").replace("\r\n", "\n") == expected_checksums
    upload_url = before["upload_url"].split("{", 1)[0] + "?name=SHA256SUMS.txt"
    api("POST", upload_url, binary=checksum_path)
    notes = (ROOT / "release_notes.md").read_text(encoding="utf-8")
    notes += "\n\nPublished source: https://github.com/" + REPO + "/commit/" + CONFIG["main_commit"] + "\n\nMerged-main checks:\n"
    notes += "".join("- [" + run["name"] + "](" + run["url"] + ")\n" for run in STATE["main_ci"])
    notes += "\nSHA256:\n```text\n" + expected_checksums + "```\n"
    api("PATCH", f"releases/{before['id']}", {"tag_name": CONFIG["tag"], "target_commitish": CONFIG["main_commit"],
                                             "name": before["name"], "body": notes, "draft": False, "prerelease": False})
    after = api("GET", f"releases/{before['id']}")
    assets = {asset["name"]: asset for asset in after["assets"]}
    verified = {}
    for name, expected in CONFIG["archives"].items():
        asset = assets[name]
        verify_asset(asset, expected)
        public_download(asset["browser_download_url"], RECORDS / ("downloaded_" + name), expected["sha256"])
        verified[name] = {"id": asset["id"], "url": asset["browser_download_url"],
                          "sha256": expected["sha256"], "bytes": expected["bytes"], "public_download_verified": True}
    tag_ref = api("GET", "git/ref/tags/" + CONFIG["tag"])
    tag_object = api("GET", "git/tags/" + tag_ref["object"]["sha"])
    assert tag_object["object"]["sha"] == CONFIG["main_commit"]
    assert assets[backup_name]["id"] == original["id"]
    api("DELETE", f"releases/assets/{original['id']}")
    final = api("GET", f"releases/{before['id']}")
    assert {asset["name"] for asset in final["assets"]} == set(CONFIG["archives"]) | {"SHA256SUMS.txt"}
    STATE.update(phase="published_and_verified", complete=True, source_tree=CONFIG["approved_tree"],
                 release_url=final["html_url"], release_id=final["id"], tag_target=tag_object["object"]["sha"],
                 archives=verified, original_asset_replaced=True, manuscript_modified=False)
    save()
    print("PUBLICATION_VERIFIED_JSON=" + json.dumps(STATE), flush=True)


try:
    main()
except Exception as error:
    STATE["error"] = type(error).__name__ + ": " + str(error)
    save()
    raise
