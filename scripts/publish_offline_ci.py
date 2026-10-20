"""Build and publish an offline Mac app on this repository's GitHub runner.

Only public software and pinned model snapshots are downloaded. No manuscript
or workstation data is used. Large archives are streamed into release parts,
so a runner never needs a second 10 GB copy of the weights.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote, urlsplit
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = "wutong-lab/yiyan-paperbridge"
# Existing clients pin this canonical asset prefix. GitHub redirects it to the
# renamed repository; retain it in manifests until those clients migrate.
DOWNLOAD_REPO = "wutong-lab/yiyan-paperbridge"
TAG = "v1.4.0-offline-14b"
APP = ROOT / "dist/release/译研.app"
PREFIX = "Yiyan-1.4.0-AppleSilicon-Offline-14B.tar.gz"
PART_SIZE = 1800 * 1024 * 1024


def require_runner():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise RuntimeError("This publisher runs only on the authorized repository's CI runner")


def api(path, data=None, method=None):
    token = os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request("https://api.github.com" + path,
        data=None if data is None else json.dumps(data).encode(), method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "PaperBridge-release"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return None if response.status == 204 else json.load(response)
    except urllib.error.HTTPError as exc:
        try: detail = json.loads(exc.read()).get('message', exc.reason)
        except (ValueError, UnicodeError): detail = exc.reason
        raise RuntimeError(f'GitHub API {exc.code} {path}: {detail}') from exc


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, destination, expected=None, size=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out, 4 * 1024 * 1024)
            if size is not None and partial.stat().st_size != size:
                raise RuntimeError("Download byte count mismatch: " + destination.name)
            if expected and sha(partial) != expected:
                raise RuntimeError("Download SHA256 mismatch: " + destination.name)
            partial.replace(destination)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_private_asset(asset, destination, expected):
    # Never forward the repository token to a redirected CDN/S3 URL.
    request = urllib.request.Request(asset["url"], headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/octet-stream", "User-Agent": "PaperBridge-release"})
    if urlsplit(request.full_url).hostname != "api.github.com":
        raise RuntimeError("Unexpected private asset API host")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=120) as response:
            with Path(destination).open("wb") as out:
                shutil.copyfileobj(response, out, 4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        url = exc.headers["Location"]
        if urlsplit(url).scheme != "https":
            raise RuntimeError("Unexpected download redirect")
        download(url, destination, expected=expected, size=asset["size"])
    if sha(destination) != expected:
        raise RuntimeError("Corresponding source checksum mismatch")


def bootstrap():
    require_runner()
    release = api("/repos/astral-sh/python-build-standalone/releases/tags/20260924")
    name = "cpython-3.12.14+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz"
    asset = next(a for a in release["assets"] if a["name"] == name)
    target = ROOT / ".cache/runtime.tar.gz"
    expected = (asset.get("digest") or "").removeprefix("sha256:") or None
    download(asset["browser_download_url"], target, expected=expected, size=asset["size"])
    with tarfile.open(target) as archive:
        archive.extractall(ROOT / ".cache", filter="data")
    target.unlink()
    sources = ROOT / "release-assets/vendor-sources"
    sources.mkdir(exist_ok=True)
    releases = api(f"/repos/{REPO}/releases?per_page=100")
    source = next(a for r in releases for a in r["assets"]
                  if a["name"] == "PaperBridge-1.4.0-ThirdPartySource.zip")
    target = ROOT / ".cache/thirdparty.zip"
    download_private_asset(source, target,
        "c543860d86f3562d10308f1cf080b0bb23b0777851e89c2d5e22131a9ccf02e1")
    with zipfile.ZipFile(target) as archive:
        for info in archive.infolist():
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "vendor-sources":
                raise RuntimeError("Unexpected corresponding source path")
        archive.extractall(ROOT / "release-assets")
    target.unlink()
    print("Official Python 3.12.14 and exact corresponding sources ready", flush=True)


def build():
    require_runner()
    sys.path.insert(0, str(ROOT / "scripts"))
    import package_macos as package

    def cpu_runtime_check(app):
        # Hosted Mac VMs do not provide the workstation's Metal GPU. Check
        # relocatability and PDF/UI dependencies without claiming inference QA.
        with tempfile.TemporaryDirectory(prefix="paperbridge-ci-relocation-") as folder:
            moved = Path(folder) / "Different Folder/译研.app"
            moved.parent.mkdir()
            shutil.copytree(app, moved, symlinks=True)
            resources = moved / "Contents/Resources"
            code = """import sys,pathlib,ssl,bz2,lzma,sqlite3,ctypes
import fastapi,uvicorn,httpx,pymupdf,psutil,onnxruntime,babeldoc,fontTools
assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve()
assert all(not p or pathlib.Path(p).resolve().is_relative_to(pathlib.Path(sys.argv[2]).resolve()) for p in sys.path)
print('Relocated PDF/UI runtime imports passed; Metal inference not tested on CI')
"""
            subprocess.run([str(resources / "Python/bin/python3.12"), "-c", code,
                str(resources / "Python"), folder], cwd=folder, check=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1"})

    package.smoke_test = cpu_runtime_check
    package.SCRIPTS.append("publish_offline_ci.py")
    sys.argv = ["package_macos.py", "--no-dmg", "--python-runtime", str(ROOT / ".cache/python")]
    package.main()
    resources = APP / "Contents/Resources"
    source = ROOT / "dist/release/PaperBridge-Source"
    shutil.copytree(ROOT / "release-assets/model-snapshots", source / "release-assets/model-snapshots")
    shutil.copy2(ROOT / "release-assets/layout-snapshot.json", source / "release-assets/layout-snapshot.json")
    shutil.copy2(ROOT / "README.md", source / "README.md")
    package.archive_tree(source, ROOT / "dist/release/PaperBridge-1.4.0-Source.zip", "PaperBridge-1.4.0")
    shutil.copy2(ROOT / "dist/release/PaperBridge-1.4.0-Source.zip", resources / "ApplicationSource.zip")
    plist = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
    plist["CFBundleIdentifier"] = "org.paperbridge.yiyan.offline"
    (APP / "Contents/Info.plist").write_bytes(plistlib.dumps(plist))
    (resources / "Project.plist").write_bytes(plistlib.dumps({"Portable": True, "BundledModels": True}))
    cache = ROOT / ".cache/babeldoc"
    metadata = json.loads((cache / "ready.json").read_text())
    pinned = json.loads((ROOT / "release-assets/layout-snapshot.json").read_text())
    for category in ["fonts", "models", "tiktoken", "cmap"]:
        expected = {item["name"]: item["sha3_256"] for item in pinned[category]}
        actual = {item["name"]: item["sha3_256"] for item in metadata[category]}
        if expected != actual:
            raise RuntimeError("Layout resources changed from the validated local snapshot")
    layout = resources / "LayoutAssets"
    layout.mkdir()
    for category in ["fonts", "models", "tiktoken", "cmap"]:
        (layout / category).mkdir()
        for item in metadata[category]:
            name = item["name"]
            if Path(name).name != name:
                raise RuntimeError("Unsafe layout resource name")
            path = cache / category / name
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha3_256").hexdigest()
            if actual != item["sha3_256"]:
                raise RuntimeError("Layout checksum mismatch")
            os.link(path, layout / category / name)
    shutil.copy2(cache / "ready.json", layout / "ready.json")
    report = json.loads((ROOT / "dist/release/PACKAGE_REPORT.json").read_text())
    report.update(models_included=True, relocation_smoke_test="passed_pdf_ui_imports",
                  metal_inference_test="not_run_on_hosted_vm", original_local_translation_test="54.5s synthetic 1-page, 17 regions; separate local build")
    (ROOT / "dist/release/PACKAGE_REPORT.json").write_text(json.dumps(report, indent=2))


def upload(release_id, path):
    path = Path(path)
    expected = sha(path)
    existing = api(f"/repos/{REPO}/releases/{release_id}/assets?per_page=100")
    for asset in existing:
        if asset["name"] == path.name:
            if asset.get("digest") == "sha256:" + expected and asset["size"] == path.stat().st_size:
                return asset
            raise RuntimeError("Existing release asset differs: " + path.name)
    for attempt in range(3):
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=180, blocksize=4 * 1024 * 1024)
        try:
            with path.open("rb") as stream:
                connection.request("POST", f"/repos/{REPO}/releases/{release_id}/assets?name={quote(path.name)}",
                    body=stream, headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                    "Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size),
                    "User-Agent": "PaperBridge-release"})
                response = connection.getresponse()
                data = json.loads(response.read())
            if response.status != 201 or data.get("size") != path.stat().st_size:
                raise RuntimeError("Release upload failed: " + path.name)
            if data.get("digest") and data["digest"] != "sha256:" + expected:
                raise RuntimeError("GitHub asset SHA256 mismatch")
            print("Uploaded " + path.name + " SHA256=" + expected, flush=True)
            return data
        except (OSError, http.client.HTTPException):
            existing = api(f"/repos/{REPO}/releases/{release_id}/assets?per_page=100")
            for asset in existing:
                if asset["name"] == path.name and asset.get("digest") == "sha256:" + expected and asset["size"] == path.stat().st_size:
                    return asset
            if attempt == 2:
                raise
            time.sleep(3)
        finally:
            connection.close()


class PartWriter:
    def __init__(self, folder, prefix, on_part, part_size=PART_SIZE):
        self.folder, self.prefix, self.on_part, self.part_size = Path(folder), prefix, on_part, part_size
        self.folder.mkdir(parents=True, exist_ok=True)
        self.parts, self.stream, self.count, self.total = [], None, 0, 0
        self.full_hash = hashlib.sha256()

    def write(self, data):
        length = len(data)
        view = memoryview(data)
        self.full_hash.update(view)
        self.total += length
        while view:
            if self.stream is None:
                self.path = self.folder / f"{self.prefix}.part{len(self.parts)+1:03d}"
                self.stream = self.path.open("wb")
                self.part_hash, self.count = hashlib.sha256(), 0
            portion = view[:self.part_size - self.count]
            self.stream.write(portion)
            self.part_hash.update(portion)
            self.count += len(portion)
            view = view[len(portion):]
            if self.count == self.part_size:
                self.finish_part()
        return length

    def flush(self):
        if self.stream is not None:
            self.stream.flush()

    def finish_part(self):
        if self.stream is not None:
            self.stream.close()
            record = {"file": self.path.name, "bytes": self.count, "sha256": self.part_hash.hexdigest()}
            self.on_part(self.path)
            self.parts.append(record)
            self.path.unlink()
            self.stream = None


def publish():
    require_runner()
    resources = APP / "Contents/Resources"
    records = []
    for name in ["qwen3-14b", "qwen3-4b"]:
        metadata = json.loads((ROOT / "release-assets/model-snapshots" / (name + ".json")).read_text())
        target = resources / "Models" / name
        target.mkdir(parents=True)
        for item in metadata["files"]:
            filename = item["file"]
            if Path(filename).name != filename:
                raise RuntimeError("Unsafe model filename")
            print("Downloading pinned " + name + "/" + filename, flush=True)
            download(f"https://huggingface.co/{metadata['repo']}/resolve/{metadata['revision']}/{quote(filename)}?download=true",
                     target / filename, expected=item["sha256"], size=item["size"])
        (target / "installation.json").write_text(json.dumps(metadata, indent=2))
        bits = json.loads((target / "config.json").read_text())["quantization"]["bits"]
        if bits != 4:
            raise RuntimeError("Unexpected quantization precision")
        records.append({"directory": name, "repo": metadata["repo"], "revision": metadata["revision"],
                        "bytes": sum(i["size"] for i in metadata["files"]), "quantization_bits": bits,
                        "snapshot_sha256_verified": True})
    (resources / "BUNDLED_MODELS.json").write_text(json.dumps({"models": records, "layout_sha3_verified": True}, indent=2))
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(APP)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    release = api(f"/repos/{REPO}/releases", {
        "tag_name": TAG, "target_commitish": os.environ["GITHUB_SHA"], "name": "译研 1.4 · 含 14B 的完整离线 Mac 版",
        "body": "正在上传完整离线应用，校验完成后才会正式发布。", "draft": True})
    writer = PartWriter(ROOT / ".cache/release-parts", PREFIX, lambda path: upload(release["id"], path))
    with tarfile.open(fileobj=writer, mode="w|gz", dereference=False) as archive:
        archive.add(APP, arcname="译研.app")
    writer.finish_part()
    out = ROOT / "dist/release"
    manifest = {"output": PREFIX, "bytes": writer.total, "sha256": writer.full_hash.hexdigest(), "parts": writer.parts}
    (out / "MAC-PARTS.json").write_text(json.dumps(manifest, indent=2))
    checks = "\n".join(p["sha256"] + "  " + p["file"] for p in writer.parts) + "\n"
    (out / "MAC-PARTS-SHA256.txt").write_text(checks)
    merge = out / "合并完整应用.command"
    names = " ".join('"' + p["file"] + '"' for p in writer.parts)
    merge.write_text('#!/bin/zsh\nset -eu\ncd "${0:A:h}"\n/usr/bin/shasum -a 256 -c MAC-PARTS-SHA256.txt\nOUTPUT="' + PREFIX + '"\nif [[ -e "$OUTPUT" ]]; then\n  echo "已有安装文件，正在校验。"\nelse\n  TEMP="$OUTPUT.merging"\n  trap \'rm -f "$TEMP"\' EXIT\n  /bin/cat ' + names + ' > "$TEMP"\n  echo "' + manifest["sha256"] + '  $TEMP" | /usr/bin/shasum -a 256 -c -\n  /bin/mv -n "$TEMP" "$OUTPUT"\nfi\necho "' + manifest["sha256"] + '  $OUTPUT" | /usr/bin/shasum -a 256 -c -\n/usr/bin/open "$OUTPUT"\necho "解压后将译研拖入应用程序。"\n')
    merge.chmod(0o755)
    with zipfile.ZipFile(out / "Full-Mac-Merge-Tools.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in [merge, out / "MAC-PARTS.json", out / "MAC-PARTS-SHA256.txt"]:
            archive.write(file, "合并工具/" + file.name)
    (out / "Installation-Guide.md").write_text(f"""# 译研完整离线 Mac 版

下载全部 {len(writer.parts)} 个 `{PREFIX}.partNNN` 和 `Full-Mac-Merge-Tools.zip`。
解压工具，把所有分卷放入“合并工具”文件夹，双击 `合并完整应用.command`。
工具逐卷核对 SHA256，再校验完整归档。打开生成的 tar.gz，解压后将“译研”拖入“应用程序”。

Apple Silicon，macOS 26.2+；14B 精译建议 24GB 或更多统一内存，建议预留 35GB 可用硬盘空间。
应用内含相同快照的 Qwen3-14B 4bit、Qwen3-4B 4bit、排版模型、字体和 Python 环境。
安装后离线翻译，不使用模型 API。尚未使用 Apple Developer ID 签名或公证。
本机原安装版的一页合成样例翻译为 54.5 秒；云端重新打包只验证结构、运行环境导入与签名，未测试云端 Metal 推理。
复杂 SCI PDF 的语义与版面仍可能存在问题，扫描页尚无 OCR。原论文和译稿不包含在发行文件中。

AGPL-3.0 源码见本仓库；第三方对应源码、模型 Apache-2.0 许可及字体原许可随发行保留。
""")
    report = json.loads((out / "PACKAGE_REPORT.json").read_text())
    report.update(models=records, archive=manifest, source_commit=os.environ["GITHUB_SHA"],
                  codesign_verification="passed_adhoc", user_documents_included=False)
    (out / "PACKAGE_REPORT.json").write_text(json.dumps(report, indent=2))
    small = [out / name for name in ["MAC-PARTS.json", "MAC-PARTS-SHA256.txt", "Full-Mac-Merge-Tools.zip",
             "Installation-Guide.md", "PACKAGE_REPORT.json", "PaperBridge-1.4.0-Source.zip",
             "PaperBridge-1.4.0-ThirdPartySource.zip", "THIRD_PARTY_NOTICES.md"]]
    (out / "ASSET-SHA256.txt").write_text("\n".join(sha(p) + "  " + p.name for p in small) + "\n")
    for path in [*small, out / "ASSET-SHA256.txt"]:
        upload(release["id"], path)
    final = api(f"/repos/{REPO}/releases/{release['id']}/assets?per_page=100")
    for p in writer.parts:
        asset = next(a for a in final if a["name"] == p["file"])
        if asset["size"] != p["bytes"] or asset.get("digest") and asset["digest"] != "sha256:" + p["sha256"]:
            raise RuntimeError("Final release part verification failed")
    notes = (out / "Installation-Guide.md").read_text()
    api(f"/repos/{REPO}/releases/{release['id']}", {"draft": False, "body": notes}, method="PATCH")
    print(f"PUBLISHED https://github.com/{REPO}/releases/tag/{TAG}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["bootstrap", "build", "publish"])
    args = parser.parse_args()
    {"bootstrap": bootstrap, "build": build, "publish": publish}[args.phase]()
