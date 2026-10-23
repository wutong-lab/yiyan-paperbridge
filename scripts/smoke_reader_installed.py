"""Check reader APIs and exported markup using the actual installed application.

This creates synthetic English/Chinese PDFs without invoking translation. Native
fullscreen interaction and model translation have separate acceptance checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import uuid


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    application = p.add_mutually_exclusive_group(required=True)
    application.add_argument("--app", type=Path)
    application.add_argument("--bundle", type=Path)
    p.add_argument("--engine", type=Path, help="Development-only engine override, recorded in the report")
    p.add_argument("--data", type=Path, required=True, help="Dedicated synthetic QA directory, not user data")
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p


def runtime(args):
    if args.app:
        resources = args.app.resolve() / "Contents/Resources"
        native = plistlib.loads((args.app / "Contents/Info.plist").read_bytes())["CFBundleShortVersionString"]
        return resources / "Python/bin/python3.12", resources / "Engine", native
    root = args.bundle.resolve()
    native = json.loads((root / "BUILD-REPORT.json").read_text(encoding="utf-8-sig"))["version"]
    return root / "Runtime/Python/python.exe", root / "Engine", native


def fixture(chinese=False):
    import pymupdf as fitz
    with fitz.open() as pdf:
        for rotation in (0, 90):
            page = pdf.new_page(width=500, height=700)
            text = "中文阅读支持局部高亮和下划线。" if chinese else "Scientific reading supports highlights and underlines."
            # A rotated PDF page with horizontal display text exercises the
            # coordinate transform used by landscape scientific papers.
            page.insert_text((70, 650) if rotation else (45, 70), text,
                             fontname="china-s" if chinese else "helv", fontsize=12,
                             rotate=90 if rotation else 0)
            page.draw_rect(fitz.Rect(45, 120, 270, 200), color=(.2, .5, .3), fill=(.92, .97, .94))
            page.insert_text((60, 165), "Synthetic figure - 42", fontsize=12)
            page.set_rotation(rotation)
        pdf.set_metadata({"title": "Synthetic reader API acceptance fixture", "author": "PaperBridge QA"})
        return pdf.tobytes(deflate=True)


def worker(args):
    import pymupdf as fitz
    from fastapi.testclient import TestClient
    from paperbridge.gguf_inference import block_external_network
    block_external_network()
    import paperbridge.app as api

    _, installed_engine, native_version = runtime(args)
    expected_engine = args.engine.resolve() if args.engine else installed_engine
    assert Path(api.__file__).resolve().is_relative_to(expected_engine.resolve())
    assert api.ROOT == args.data.resolve()
    assert api.translator.model is None
    created = api.add_document(fixture(), "Synthetic-reader-acceptance.pdf")
    job = api.get_job(created["id"])
    folder = api.DATA / job["id"]
    (folder / "translated.pdf").write_bytes(fixture(chinese=True))
    job.update(status="complete", has_output=True)
    api.save_job(job)
    hashes = {version: hashlib.sha256((folder / f"{version}.pdf").read_bytes()).hexdigest()
              for version in ("original", "translated")}
    base = f"/api/documents/{job['id']}/annotations"
    cases = []
    with TestClient(api.app) as client:
        home = client.get("/")
        assert home.status_code == 200
        for identifier in ("readerFocus", "originalZoom", "translatedZoom", "annotationToolbar"):
            assert f'id="{identifier}"' in home.text, identifier
        for name in ("reader.js", "reader.css", "fullscreen.js", "annotations.js", "annotations.css", "studio.css"):
            assert f"/static/{name}" in home.text, name
            resource = client.get("/static/" + name)
            assert resource.status_code == 200 and resource.content, name
        for version in ("original", "translated"):
            listing = client.get(base, params={"version": version}).json()
            assert listing["page_count"] == 2 and not listing["annotations"]
            revision = listing["document_revision"]
            rows = []
            for page, kind, color in [(1, "highlight", "#a855f7"), (2, "underline", "#e11d48")]:
                response = client.get(base + "/text", params={"version": version, "page": page})
                assert response.status_code == 200
                text = response.json()
                assert text["document_revision"] == revision and not text["truncated"]
                marker = "中" if version == "translated" else "Scientific"
                line = next(line for line in text["lines"] if any(w["text"] == marker for w in line["words"]))
                words = line["words"][:3]
                assert words
                if version == "translated":
                    assert all(len(w["text"]) == 1 for w in words), "Chinese selection must be character precise"
                rectangle = [min(w["rect"][0] for w in words), min(w["rect"][1] for w in words),
                             max(w["rect"][2] for w in words), max(w["rect"][3] for w in words)]
                body = {"id": str(uuid.uuid4()), "version": version, "document_revision": revision,
                        "page": page, "kind": kind, "rect": rectangle, "rects": [rectangle],
                        "color": color, "note": "Synthetic reader QA"}
                saved = client.post(base, json=body)
                assert saved.status_code == 201, saved.text
                assert client.post(base, json=body).json() == saved.json(), "Save retry must be idempotent"
                rows.append(saved.json())
            # Discard the in-memory job and reload the same disk records.
            api.jobs.clear()
            reopened = client.get(base, params={"version": version}).json()
            assert reopened["annotations"] == rows
            download = client.get(base + "/export", params={"version": version, "document_revision": revision})
            assert download.status_code == 200
            assert download.headers["content-type"] == "application/pdf"
            destination = args.evidence / f"reader-{version}-annotated.pdf"
            destination.write_bytes(download.content)
            with fitz.open(stream=download.content, filetype="pdf") as pdf:
                assert len(pdf) == 2
                for page, row in zip(pdf, rows):
                    annotations = list(page.annots())
                    assert len(annotations) == 1
                    annotation = annotations[0]
                    assert annotation.type[0] == (fitz.PDF_ANNOT_HIGHLIGHT if row["kind"] == "highlight" else fitz.PDF_ANNOT_UNDERLINE)
                    expected = [int(row["color"][i:i+2], 16) / 255 for i in (1, 3, 5)]
                    assert all(abs(a - b) < .001 for a, b in zip(annotation.colors["stroke"], expected))
                    assert annotation.info["content"] == row["note"]
                    points = [fitz.Point(p) * page.rotation_matrix for p in annotation.vertices]
                    display = fitz.Quad(points).rect
                    actual = [display.x0 / page.rect.width, display.y0 / page.rect.height,
                              display.x1 / page.rect.width, display.y1 / page.rect.height]
                    assert all(abs(a - b) < .00001 for a, b in zip(actual, row["rect"]))
                    page.get_pixmap(matrix=fitz.Matrix(1, 1)).save(args.evidence / f"reader-{version}-page-{page.number+1}.png")
            for row in rows:
                url = base + "/" + row["id"]
                query = {"version": version, "document_revision": revision}
                assert client.delete(url, params=query).status_code == 200
                assert client.delete(url, params=query).status_code == 200
                old = {k: row[k] for k in ("id", "version", "document_revision", "page", "kind", "rect", "rects", "color", "note")}
                assert client.post(base, json=old).status_code == 410, "Late saves must not resurrect deleted marks"
            assert client.get(base, params={"version": version}).json()["annotations"] == []
            assert hashlib.sha256((folder / f"{version}.pdf").read_bytes()).hexdigest() == hashes[version]
            cases.append({"version": version, "persisted_after_reload": True, "source_pdf_unchanged": True,
                          "custom_colors_exported": True, "native_annotations_exported": True,
                          "rotated_page_coordinates_passed": True, "deletion_retry_and_late_save_passed": True})
        before = client.get(f"/api/documents/{job['id']}").json()["output_revision"]
        (folder / "translated.pdf").write_bytes(fixture(chinese=False))
        after = client.get(f"/api/documents/{job['id']}").json()["output_revision"]
        assert before and after != before
        assert client.get(base + "/export", params={"version": "translated", "document_revision": revision}).status_code == 409
        assert client.get("/api/desktop/status").json()["busy"] is False
    assert api.translator.model is None
    report = {"version": args.version, "native_version": native_version, "platform": sys.platform,
              "actual_installed_runtime": True, "development_source_override": bool(args.engine),
              "installed_engine_verified": not bool(args.engine), "cases": cases,
              "reader_assets_served": True, "independent_zoom_controls_present": True,
              "fullscreen_control_and_asset_present": True, "native_fullscreen_interaction_tested": False,
              "output_revision_changes_on_reexport": True, "old_revision_export_rejected": True,
              "translation_tested": False, "synthetic_translated_fixture": True,
              "model_loaded": False, "user_credential_used": False, "external_network_blocked": True,
              "passed": True}
    (args.evidence / "READER-INSTALLED-REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


def main():
    args = parser().parse_args()
    if args.worker:
        return worker(args)
    python, engine, native = runtime(args)
    if not args.engine:
        assert native == args.version, (native, args.version)
    args.data = args.data.resolve()
    args.evidence = args.evidence.resolve()
    marker = args.data / ".reader-qa"
    if args.data.exists() and any(args.data.iterdir()) and not marker.exists():
        raise RuntimeError("Use an empty dedicated reader QA data directory; existing user data is not accepted")
    args.data.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PAPERBRIDGE_HOME": str(args.data), "PAPERBRIDGE_APP_VERSION": args.version,
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if args.bundle:
        env.update(PAPERBRIDGE_WINDOWS_BUNDLE=str(args.bundle.resolve()),
                   PAPERBRIDGE_LLAMA_DIR=str(args.bundle.resolve() / "Runtime/llama"), PAPERBRIDGE_CPU_ONLY="1")
    if args.engine:
        engine = args.engine.resolve()
    bridge = "import sys;sys.path.insert(0,sys.argv.pop(1));target=sys.argv.pop(1);sys.argv[0]=target;import runpy;runpy.run_path(target,run_name='__main__')"
    command = [str(python), "-X", "utf8", "-c", bridge, str(engine), str(Path(__file__).resolve()),
               "--app" if args.app else "--bundle", str((args.app or args.bundle).resolve()),
               "--data", str(args.data), "--evidence", str(args.evidence), "--version", args.version, "--worker"]
    if args.engine:
        command += ["--engine", str(args.engine.resolve())]
    log = args.evidence / "reader-installed.log"
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=engine, env=env, stdout=stream, stderr=stream, timeout=180)
    if result.returncode:
        print(log.read_text(encoding="utf-8", errors="replace")[-8000:])
        raise SystemExit(result.returncode)
    print((args.evidence / "READER-INSTALLED-REPORT.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
