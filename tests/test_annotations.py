import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from paperbridge.annotations import AnnotationCreate, AnnotationError, add_annotation, document_info, highlight_opacity, list_annotations


def make_pdf(path, rotation=0, text="Original scientific paper", crop=False):
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=700)
        page.insert_text((80, 100), text)
        page.insert_text((80, 120), "A second line for multiple annotation quads")
        old = page.add_text_annot((90, 140), "Existing note")
        old.set_info(title="Existing author")
        if crop:
            page.set_cropbox(fitz.Rect(25, 40, 475, 660))
        page.set_rotation(rotation)
        doc.save(path)


@pytest.fixture
def reader(tmp_path, monkeypatch):
    import paperbridge.app as api
    job_id = "e" * 32
    folder = tmp_path / job_id
    folder.mkdir()
    make_pdf(folder / "original.pdf")
    make_pdf(folder / "translated.pdf", text="Translated scientific paper")
    job = {"id": job_id, "name": "论文.pdf", "status": "complete", "has_output": True,
           "segments": [], "page_count": 1}
    monkeypatch.setattr(api, "DATA", tmp_path)
    monkeypatch.setattr(api, "jobs", {job_id: job})
    with TestClient(api.app) as client:
        yield client, f"/api/documents/{job_id}/annotations", folder, job


def payload(client, base, version="original", **changes):
    listing = client.get(base, params={"version": version})
    assert listing.status_code == 200, listing.text
    return {"version": version, "document_revision": listing.json()["document_revision"],
            "page": 1, "kind": "highlight", "rect": [0.1, 0.2, 0.6, 0.25],
            "color": "#facc15", "note": "需要进一步讨论", **changes}


def test_both_versions_persist_and_delete_independently(reader):
    client, base, folder, _ = reader
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("*.pdf")}
    for version in ("original", "translated"):
        response = client.post(base, json=payload(client, base, version))
        assert response.status_code == 201, response.text
        item = response.json()
        assert client.get(base, params={"version": version}).json()["annotations"] == [item]
    data = json.loads((folder / "annotations.json").read_text())
    assert len(data["annotations"]["original"]) == len(data["annotations"]["translated"]) == 1
    translated = data["annotations"]["translated"][0]
    response = client.delete(base + "/" + translated["id"], params={
        "version": "translated", "document_revision": translated["document_revision"]})
    assert response.status_code == 200
    assert not list_annotations(folder, "translated")["annotations"]
    assert len(list_annotations(folder, "original")["annotations"]) == 1
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("*.pdf")} == hashes


def test_revision_change_retains_old_notes_without_misplacing_them(reader):
    client, base, folder, _ = reader
    old = payload(client, base, "translated")
    item = client.post(base, json=old).json()
    (folder / "translated.pdf").unlink()
    make_pdf(folder / "translated.pdf", text="New version with changed translation")
    listing = client.get(base, params={"version": "translated"}).json()
    assert listing["stale_count"] == 1 and listing["annotations"] == []
    assert client.post(base, json=old).status_code == 409
    assert client.delete(base + "/" + item["id"], params={
        "version": "translated", "document_revision": old["document_revision"]}).status_code == 409
    assert client.get(base + "/export", params={
        "version": "translated", "document_revision": old["document_revision"]}).status_code == 409
    assert len(json.loads((folder / "annotations.json").read_text())["annotations"]["translated"]) == 1


def test_client_id_makes_retry_idempotent(reader):
    client, base, _, _ = reader
    body = payload(client, base, id=str(uuid.uuid4()))
    first = client.post(base, json=body)
    second = client.post(base, json=body)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(client.get(base).json()["annotations"]) == 1
    assert client.post(base, json={**body, "note": "different"}).status_code == 409


@pytest.mark.parametrize("arrived_before_delete", [True, False])
def test_deleted_client_uuid_cannot_be_resurrected_by_delayed_save(reader, arrived_before_delete):
    client, base, folder, _ = reader
    body = payload(client, base, id=str(uuid.uuid4()))
    if arrived_before_delete:
        assert client.post(base, json=body).status_code == 201
    delete_url = base + "/" + body["id"]
    params = {"version": "original", "document_revision": body["document_revision"]}
    assert client.delete(delete_url, params=params).status_code == 200
    # A lost deletion response can safely be retried, even after a restart: the
    # deletion ledger is persisted, rather than a browser-only pending flag.
    assert json.loads((folder / "annotations.json").read_text())["deleted"]["original"][0]["id"] == body["id"]
    assert client.delete(delete_url, params=params).status_code == 200
    assert client.post(base, json=body).status_code == 410
    assert client.get(base).json()["annotations"] == []
    assert client.post(base, json={**body, "id": str(uuid.uuid4())}).status_code == 201


@pytest.mark.parametrize("rect", [[0.5, 0.2, 0.1, 0.3], [-0.1, 0, 0.2, 0.3],
                                 [0, 0, 1.1, 1], [0, 0, 0, 1], [0, 0, 1],
                                 [0, 0, float("nan"), 1]])
def test_invalid_coordinates_are_rejected(rect):
    with pytest.raises(ValidationError):
        AnnotationCreate(version="original", document_revision="a" * 64, page=1,
                         kind="highlight", rect=rect)


def test_invalid_page_version_and_extra_inputs(reader):
    client, base, _, _ = reader
    body = payload(client, base)
    assert client.post(base, json={**body, "page": 2}).status_code == 400
    assert client.post(base, json={**body, "version": "../../elsewhere"}).status_code == 422
    assert client.post(base, json={**body, "color": "red;bad"}).status_code == 422
    assert client.post(base, json={**body, "unknown": "field"}).status_code == 422
    assert client.get(base + "/text?page=0").status_code == 400
    assert client.get(base.replace("e" * 32, "missing")).status_code == 404


def test_unavailable_or_running_translation_leaves_original_readable(reader):
    client, base, _, job = reader
    job["has_output"] = False
    assert client.get(base + "?version=translated").status_code == 404
    job.update(has_output=True, status="running")
    assert client.get(base + "?version=translated").status_code == 409
    assert client.get(base).status_code == 200


def test_corrupt_or_symlink_storage_never_overwritten(reader, tmp_path):
    client, base, folder, _ = reader
    body = payload(client, base)
    store = folder / "annotations.json"
    store.write_text("{unreadable", encoding="utf-8")
    assert client.post(base, json=body).status_code == 409
    assert store.read_text() == "{unreadable"
    store.unlink()
    outside = tmp_path / "notes.json"
    outside.write_text("protect this")
    store.symlink_to(outside)
    assert client.post(base, json=body).status_code == 400
    assert outside.read_text() == "protect this"


def test_pdf_symlink_is_rejected(reader, tmp_path):
    client, base, folder, _ = reader
    source = folder / "original.pdf"
    outside = tmp_path / "outside.pdf"
    source.rename(outside)
    source.symlink_to(outside)
    assert client.get(base).status_code == 400


def test_simultaneous_saves_do_not_lose_annotations(reader):
    client, base, folder, _ = reader
    body = payload(client, base)
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(lambda n: add_annotation(folder, AnnotationCreate(**{
            **body, "note": f"Note {n}"})), range(30)))
    persisted = list_annotations(folder, "original")["annotations"]
    assert {item["id"] for item in persisted} == {item["id"] for item in items}
    assert len(persisted) == 30


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("kind", ["highlight", "underline"])
def test_export_native_markup_preserves_rotation_crop_and_existing_notes(reader, rotation, kind):
    client, base, folder, _ = reader
    source = folder / "original.pdf"
    source.unlink()
    make_pdf(source, rotation=rotation, crop=True)
    before = source.read_bytes()
    rects = [[0.1, 0.2, 0.7, 0.24], [0.1, 0.3, 0.5, 0.34]]
    body = payload(client, base, kind=kind, rects=rects)
    response = client.post(base, json=body)
    assert response.status_code == 201, response.text
    output = client.get(base + "/export")
    assert output.status_code == 200 and output.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''" in output.headers["content-disposition"]
    assert source.read_bytes() == before
    with fitz.open(stream=output.content, filetype="pdf") as doc:
        page = doc[0]
        assert page.rotation == rotation
        annotations = list(page.annots())
        assert len(annotations) == 2
        old, new = annotations
        assert old.info["content"] == "Existing note"
        assert new.type[0] == (fitz.PDF_ANNOT_HIGHLIGHT if kind == "highlight" else fitz.PDF_ANNOT_UNDERLINE)
        assert new.info["content"] == body["note"]
        assert len(new.vertices) == 8
        for index, r in enumerate(rects):
            expected = [(r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])]
            for raw, normalized in zip(new.vertices[index * 4:index * 4 + 4], expected):
                point = fitz.Point(raw) * page.rotation_matrix
                assert point.x / page.rect.width == pytest.approx(normalized[0], abs=1e-6)
                assert point.y / page.rect.height == pytest.approx(normalized[1], abs=1e-6)
        # Ensure all native annotations have usable appearance streams after reopen.
        assert len(page.get_pixmap().samples) > 0


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_boxes_match_display_coordinates(reader, rotation):
    client, base, folder, _ = reader
    source = folder / "original.pdf"
    source.unlink()
    make_pdf(source, rotation=rotation, crop=True)
    result = client.get(base + "/text").json()
    assert result["page"] == 1 and not result["truncated"]
    assert result["document_revision"] == client.get(base).json()["document_revision"]
    words = [word for line in result["lines"] for word in line["words"]]
    assert any(word["text"] == "Original" for word in words)
    with fitz.open(source) as doc:
        page = doc[0]
        raw = next(w for w in page.get_text("words") if w[4] == "Original")
        display = fitz.Rect(raw[:4]) * page.rotation_matrix
        word = next(w for w in words if w["text"] == "Original")
        assert word["rect"] == pytest.approx([display.x0 / page.rect.width, display.y0 / page.rect.height,
                                             display.x1 / page.rect.width, display.y1 / page.rect.height])


def test_unchanged_pdf_metadata_uses_cached_hash(reader, monkeypatch):
    _, _, folder, _ = reader
    source = folder / "original.pdf"
    first = document_info(source)
    monkeypatch.setattr(hashlib, "sha256", lambda *a, **kw: pytest.fail("unchanged PDF rehashed"))
    assert document_info(source) == first


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_chinese_selection_uses_character_boxes_instead_of_entire_line(reader, rotation):
    client, base, folder, _ = reader
    source = folder / "translated.pdf"
    source.unlink()
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text((40, 60), "中文译文支持局部高亮，不会选中整行。 English words", fontname="china-s")
        page.set_rotation(rotation)
        doc.save(source)
    result = client.get(base + "/text?version=translated").json()
    words = [word for line in result["lines"] for word in line["words"]]
    assert not result["truncated"]
    assert "".join(word["text"] for word in words) == "中文译文支持局部高亮，不会选中整行。Englishwords"
    assert [word["text"] for word in words if word["text"] in {"English", "words"}] == ["English", "words"]
    with fitz.open(source) as doc:
        page = doc[0]
        selected = page.search_for("局部高亮")[0] * page.rotation_matrix
        normalized = [selected.x0 / page.rect.width, selected.y0 / page.rect.height,
                      selected.x1 / page.rect.width, selected.y1 / page.rect.height]
        hit = [word for word in words if normalized[0] < (word["rect"][0] + word["rect"][2]) / 2 < normalized[2]
               and normalized[1] < (word["rect"][1] + word["rect"][3]) / 2 < normalized[3]]
        assert "".join(word["text"] for word in hit) == "局部高亮"
        assert len(hit) == 4


def test_cjk_extraction_excludes_images_and_bounds_character_response(reader, monkeypatch):
    import paperbridge.annotations as annotations
    client, base, folder, _ = reader
    source = folder / "translated.pdf"
    source.unlink()
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), "中文译文支持局部高亮", fontname="china-s")
        doc.save(source)
    original = fitz.Page.get_text
    calls = []
    def checked(page, option="text", *args, **kwargs):
        if option == "rawdict":
            calls.append(kwargs["flags"])
            assert not kwargs["flags"] & fitz.TEXT_PRESERVE_IMAGES
        return original(page, option, *args, **kwargs)
    monkeypatch.setattr(fitz.Page, "get_text", checked)
    monkeypatch.setattr(annotations, "MAX_WORD_BOXES", 3)
    result = client.get(base + "/text?version=translated").json()
    assert calls and result["truncated"]
    assert [w["text"] for line in result["lines"] for w in line["words"]] == ["中", "文", "译"]


def test_automatic_and_manual_cleanup_preserve_reader_annotations(reader):
    from paperbridge.storage import cleanup_targets, clear_optional_files
    from paperbridge.storage_cleanup import cleanup_after_task

    client, base, folder, job = reader
    assert client.post(base, json=payload(client, base)).status_code == 201
    job.update(export_report={"structural_checks_passed": True}, settings={"auto_cleanup": True})
    (folder / "job.json").write_text(json.dumps(job))
    (folder / "bilingual.pdf").write_bytes((folder / "translated.pdf").read_bytes())
    (folder / "translated.new.pdf").write_bytes(b"temporary")
    (folder / "full-worker.log").write_text("log")
    original = (folder / "annotations.json").read_bytes()
    targets, errors = cleanup_targets(folder.parent, folder.parent)
    assert not errors
    assert all(folder / "annotations.json" not in items for items in targets.values())
    cleanup_after_task(folder, job, succeeded=True, force=True)
    clear_optional_files(folder.parent, folder.parent, ["temporary", "backups", "downloads", "logs"])
    assert (folder / "annotations.json").read_bytes() == original
    assert not (folder / "translated.new.pdf").exists()
    assert not (folder / "full-worker.log").exists()


@pytest.mark.parametrize("operation", ["save", "delete", "export"])
def test_native_quit_and_cleanup_see_annotation_worker_until_it_finishes(reader, monkeypatch, operation):
    import paperbridge.app as api
    client, base, _, _ = reader
    monkeypatch.setattr(api, "active_operations", set())
    monkeypatch.setattr(api, "install_state", {"status": "idle"})
    body = payload(client, base)
    existing = client.post(base, json=body).json()
    entered, release = threading.Event(), threading.Event()
    function = {"save": "add_annotation", "delete": "delete_annotation", "export": "export_annotations"}[operation]
    original = getattr(api, function)
    def paused(*args):
        entered.set()
        assert release.wait(5), "test annotation worker was not released"
        return original(*args)
    monkeypatch.setattr(api, function, paused)
    def request():
        if operation == "save":
            return client.post(base, json=body)
        if operation == "delete":
            return client.delete(base + "/" + existing["id"], params={"document_revision": body["document_revision"]})
        return client.get(base + "/export")
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(request)
        try:
            assert entered.wait(3)
            # Separate HTTP calls must remain responsive while the disk worker is
            # paused: holding state_lock through the write would deadlock here.
            assert client.get("/api/desktop/status").json()["busy"] is True
            assert client.post("/api/cleanup", json={"memory": True}).status_code == 409
            assert len(api.active_operations) == 1
            assert next(iter(api.active_operations)).startswith(f"annotation-{operation}-")
        finally:
            release.set()
        assert pending.result(timeout=3).status_code in {200, 201}
    assert not api.active_operations
    assert client.get("/api/desktop/status").json()["busy"] is False


@pytest.mark.parametrize("operation", ["save", "delete", "export"])
@pytest.mark.parametrize("failure", ["io", "revision"])
def test_annotation_failure_releases_only_its_native_activity(reader, monkeypatch, operation, failure):
    import paperbridge.app as api
    client, base, _, _ = reader
    body = payload(client, base)
    monkeypatch.setattr(api, "active_operations", {"another-operation"})
    function = {"save": "add_annotation", "delete": "delete_annotation", "export": "export_annotations"}[operation]
    def failed(*args):
        assert len(api.active_operations) == 2
        raise OSError("disk failed") if failure == "io" else AnnotationError("revision changed", 409)
    monkeypatch.setattr(api, function, failed)
    if operation == "save":
        response = client.post(base, json=body)
    elif operation == "delete":
        response = client.delete(base + "/missing", params={"document_revision": body["document_revision"]})
    else:
        response = client.get(base + "/export")
    assert response.status_code == (503 if failure == "io" else 409)
    assert api.active_operations == {"another-operation"}


def test_failed_annotation_path_lookup_does_not_leave_native_app_busy(reader, monkeypatch):
    import paperbridge.app as api
    client, base, _, _ = reader
    body = payload(client, base)
    monkeypatch.setattr(api, "active_operations", set())
    assert client.post(base.replace("e" * 32, "missing"), json=body).status_code == 404
    assert not api.active_operations


def test_job_summary_tracks_pdf_reexports_without_changing_translation_timing(reader):
    client, base, folder, job = reader
    job["timing"] = {"started_at": 1, "finished_at": 2, "elapsed_seconds": 1}
    job_url = base.removesuffix("/annotations")
    before = client.get(job_url).json()
    assert before["output_revision"] and "/" not in before["output_revision"]
    (folder / "translated.pdf").write_bytes(b"replacement export after a manual paragraph edit")
    after = client.get(job_url).json()
    assert after["output_revision"] != before["output_revision"]
    assert after["timing"] == before["timing"]
    job["has_output"] = False
    assert client.get(job_url).json()["output_revision"] == ""
    job["has_output"] = True
    (folder / "translated.pdf").unlink()
    assert client.get(job_url).json()["output_revision"] == ""


def test_export_rejects_pdf_replacement_after_loading_old_annotations(reader, monkeypatch):
    import paperbridge.annotations as annotations
    client, base, folder, _ = reader
    body = payload(client, base)
    assert client.post(base, json=body).status_code == 201
    original = annotations.list_annotations
    def replaced_after_snapshot(*args):
        snapshot = original(*args)
        make_pdf(folder / "original.pdf", text="A replacement PDF with different paragraph positions")
        return snapshot
    monkeypatch.setattr(annotations, "list_annotations", replaced_after_snapshot)
    # Without capturing the signature before list_annotations, the new PDF's
    # signature becomes the baseline and old marks silently land on new text.
    response = client.get(base + "/export", params={"document_revision": body["document_revision"]})
    assert response.status_code == 409


def test_text_boxes_reject_replacement_after_reading_old_revision(reader, monkeypatch):
    import paperbridge.annotations as annotations
    client, base, folder, _ = reader
    original = annotations.document_info
    def replaced_after_metadata(*args):
        info = original(*args)
        make_pdf(folder / "original.pdf", text="A replacement PDF with new text boxes")
        return info
    monkeypatch.setattr(annotations, "document_info", replaced_after_metadata)
    assert client.get(base + "/text").status_code == 409


@pytest.mark.parametrize("operation", ["list", "save", "delete"])
def test_snapshot_race_during_storage_read_does_not_return_or_mutate_old_marks(reader, monkeypatch, operation):
    import paperbridge.annotations as annotations
    client, base, folder, _ = reader
    body = payload(client, base)
    saved = client.post(base, json=body).json()
    before = (folder / "annotations.json").read_bytes()
    original = annotations._load
    def replaced_after_loading(*args):
        data = original(*args)
        make_pdf(folder / "original.pdf", text="A replacement PDF produced while loading reader notes")
        return data
    monkeypatch.setattr(annotations, "_load", replaced_after_loading)
    if operation == "save":
        response = client.post(base, json=body)
    elif operation == "delete":
        response = client.delete(base + "/" + saved["id"], params={"document_revision": body["document_revision"]})
    else:
        response = client.get(base)
    assert response.status_code == 409
    assert (folder / "annotations.json").read_bytes() == before


def test_replaced_pdf_native_error_returns_retryable_conflict(reader, monkeypatch):
    client, base, folder, _ = reader
    payload(client, base)  # Prime the unchanged-document metadata cache.
    replacement = (folder / "translated.pdf").read_bytes()
    def replaced_while_opening(*args, **kwargs):
        (folder / "original.pdf").write_bytes(replacement)
        raise fitz.FileDataError("PDF changed while opening")
    monkeypatch.setattr(fitz, "open", replaced_while_opening)
    assert client.get(base + "/text").status_code == 409


@pytest.mark.parametrize("color", ["#FDE047", "#fecdd3", "#a855f7", "#000000", "#ffffff", "#0000ff", "#15432e"])
def test_custom_highlight_export_preserves_color_and_readable_opacity(reader, color):
    client, base, _, _ = reader
    response = client.post(base, json=payload(client, base, color=color))
    assert response.status_code == 201
    assert response.json()["color"] == color.lower()
    output = client.get(base + "/export")
    assert output.status_code == 200
    with fitz.open(stream=output.content, filetype="pdf") as doc:
        page = doc[0]
        native = list(page.annots())[-1]
        rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        assert native.colors["stroke"] == pytest.approx(rgb, abs=1e-6)
        assert native.opacity == pytest.approx(highlight_opacity(color), abs=1e-6)
        channels = [1 - native.opacity + native.opacity * v for v in rgb]
        luminance = sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
                        for weight, v in zip((.2126, .7152, .0722), channels))
        assert luminance >= .30 - 1e-6
