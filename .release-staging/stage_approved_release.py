"""Stage byte-identical approved archives with read-only repository access."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
REPO = "sijunpeng123/Biotrack_Studio"
delta = json.loads((ROOT / "approved_archives.delta.json").read_text())
assert delta["source_commit"] == "714897020703bd61b1ebbf76608473a227ba4dfb"
assert delta["base_sha256"] == "7a6615facee58b8aaa6e96697ec6e9d0b1c16aa8e22124cebd71266d6b710981"
output = Path(os.environ["RUNNER_TEMP"]) / "approved_biotrack_release"
output.mkdir()
original = output / "original_release.zip"
request = urllib.request.Request(delta["base_url"], headers={"User-Agent": "BioTrack-Release-Stager"})
with urllib.request.urlopen(request, timeout=180) as response, original.open("wb") as stream:
    shutil.copyfileobj(response, stream)
base = original.read_bytes()
assert hashlib.sha256(base).hexdigest() == delta["base_sha256"]
records = {}
for name, expected in delta["archives"].items():
    assert name in {"BioTrack_Studio_v1.0.0.zip", "BioTrack_Studio_v1.0.0_GitHub_Source.zip"}
    target = output / name
    with target.open("wb") as stream:
        for segment in expected["segments"]:
            if "copy" in segment:
                offset, length = segment["copy"]
                assert offset >= 0 and length >= 0 and offset + length <= len(base)
                stream.write(base[offset:offset+length])
            else:
                stream.write(base64.b64decode(segment["data"], validate=True))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.stat().st_size == expected["bytes"] and digest == expected["sha256"], name
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
    records[name] = {"sha256": digest, "bytes": target.stat().st_size}
headers = {"User-Agent": "BioTrack-Release-Stager", "Accept": "application/vnd.github+json",
           "Authorization": "Bearer " + os.environ["GH_TOKEN"]}
url = f"https://api.github.com/repos/{REPO}/git/trees/{delta['source_tree']}?recursive=1"
with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
    tree = json.load(response)
blobs = {item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "blob"}
with zipfile.ZipFile(output / "BioTrack_Studio_v1.0.0_GitHub_Source.zip") as archive:
    files = {}
    for name in archive.namelist():
        relative = name.split("/", 1)[1]
        data = archive.read(name)
        files[relative] = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
assert files == blobs and len(files) == 106
checksums = "".join(f"{item['sha256']}  {name}\n" for name, item in records.items())
(output / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")
record = {"source_commit": delta["source_commit"], "byte_identical_to_approved": True,
          "source_git_blobs_verified": len(files), "archives": records,
          "scope": "Actions artifact staging only; no main, tag or release mutation"}
(output / "staging_verification.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print("STAGING_VERIFIED_JSON=" + json.dumps(record), flush=True)
