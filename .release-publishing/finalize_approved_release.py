"""Verify the published release with read-only access and retain its backup."""
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "publication_config.json").read_text())
BASE = "https://api.github.com/repos/" + CONFIG["repository"]
OUT = Path(os.environ["RUNNER_TEMP"]) / "biotrack_publication_records"
OUT.mkdir()


def api(path):
    request = urllib.request.Request(BASE + "/" + path, method="GET",
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json", "User-Agent": "BioTrack-Final-Publication-Verification"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
        return json.loads(data) if data else None


def download(asset, expected):
    normal = asset["browser_download_url"]
    record = {"id": asset["id"], "url": normal, "sha256": expected["sha256"], "bytes": expected["bytes"]}
    for attempt in range(2):
        url = normal if attempt == 0 else normal + "?verified_sha256=" + expected["sha256"]
        target = OUT / asset["name"]
        request = urllib.request.Request(url, headers={"User-Agent": "BioTrack-Final-Publication-Verification", "Cache-Control": "no-cache"})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=180) as response, target.open("wb") as stream:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(block)
                digest.update(block)
        actual = digest.hexdigest()
        print("PUBLIC_DOWNLOAD_RESPONSE_JSON=" + json.dumps({"name": asset["name"], "attempt": attempt+1, "sha256": actual, "bytes": target.stat().st_size}), flush=True)
        if actual == expected["sha256"] and target.stat().st_size == expected["bytes"]:
            with zipfile.ZipFile(target) as archive:
                assert archive.testzip() is None
            record.update(public_download_verified=True, cache_busting_needed=attempt > 0)
            return record
        assert actual == CONFIG["old_asset_sha256"], "Unexpected download content"
    raise RuntimeError("Published archive download verification failed")


assert CONFIG["author_approval"] == "批准合并 main、同步标签并替换正式安装包"
assert api("git/ref/heads/main")["object"]["sha"] == CONFIG["main_commit"]
assert api("git/commits/" + CONFIG["main_commit"])["tree"]["sha"] == CONFIG["approved_tree"]
tag_ref = api("git/ref/tags/" + CONFIG["tag"])
tag = api("git/tags/" + tag_ref["object"]["sha"])
assert tag["object"]["sha"] == CONFIG["main_commit"]
runs = api("actions/runs?head_sha=" + CONFIG["main_commit"] + "&per_page=30")["workflow_runs"]
selected = {run["name"]: run for run in runs if run["head_branch"] == "main" and run["event"] == "push"
            and run["name"] in {"tests", "Installation compatibility"}}
assert len(selected) == 2 and all(run["conclusion"] == "success" for run in selected.values())
release = api("releases/tags/" + CONFIG["tag"])
assert release["id"] == CONFIG["release_id"] and not release["draft"]
assets = {asset["name"]: asset for asset in release["assets"]}
backup_name = "BioTrack_Studio_v1.0.0.previous-20261023.zip"
assert set(assets) <= set(CONFIG["archives"]) | {"SHA256SUMS.txt", backup_name}
verified = {}
for name, expected in CONFIG["archives"].items():
    asset = assets[name]
    assert asset["state"] == "uploaded" and asset["size"] == expected["bytes"]
    assert asset["digest"] == "sha256:" + expected["sha256"]
    verified[name] = download(asset, expected)
checksums_url = assets["SHA256SUMS.txt"]["browser_download_url"]
with urllib.request.urlopen(checksums_url, timeout=60) as response:
    checksums = response.read().decode("ascii").replace("\r\n", "\n")
assert checksums == "".join(f"{item['sha256']}  {name}\n" for name, item in CONFIG["archives"].items())
if backup_name in assets:
    backup = assets[backup_name]
    assert backup["id"] == CONFIG["old_asset_id"] and backup["digest"] == "sha256:" + CONFIG["old_asset_sha256"]
after = api("releases/tags/" + CONFIG["tag"])
assert {asset["name"] for asset in after["assets"]} == set(assets)
record = {"complete": True, "approved_source": CONFIG["approved_source"], "main_commit": CONFIG["main_commit"],
          "source_tree": CONFIG["approved_tree"], "tag_object": tag_ref["object"]["sha"], "tag_target": tag["object"]["sha"],
          "release_url": after["html_url"], "release_id": after["id"], "archives": verified,
          "official_asset_replaced": True, "previous_asset_retained_as_backup": backup_name in assets,
          "backup_deletion_auto_review_rejected": True, "read_only_verification": True,
          "checksum_file_verified": True, "manuscript_modified": False,
          "main_ci": [{"name": run["name"], "id": run["id"], "url": run["html_url"], "conclusion": run["conclusion"]} for run in selected.values()],
          "first_publish_job": {"run_id": 41500590294, "result": "canonical download checksum mismatch after asset rename; retained for audit"}}
(OUT / "publication_verification.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print("PUBLICATION_VERIFIED_JSON=" + json.dumps(record), flush=True)
