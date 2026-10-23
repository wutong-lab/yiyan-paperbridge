"""Persistent reader markup, anchored to an exact PDF revision and display coordinates."""
import copy
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import pymupdf as fitz
from pydantic import BaseModel, Field, model_validator

from .pdf_engine import PDF_LOCK

LOCK = threading.RLock()
_DOCUMENTS = OrderedDict()
MAX_ANNOTATIONS = 20000
MAX_WORD_BOXES = 50000
CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef\U00020000-\U000323af]")
ANNOTATION_ID = r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"


class AnnotationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class AnnotationCreate(BaseModel):
    model_config = {"extra": "forbid"}
    id: str | None = Field(default=None, pattern=f"^{ANNOTATION_ID}$")
    version: Literal["original", "translated"]
    document_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1, strict=True)
    kind: Literal["highlight", "underline"]
    rect: list[float] = Field(min_length=4, max_length=4)
    rects: list[list[float]] | None = Field(default=None, min_length=1, max_length=100)
    color: str = Field(default="#facc15", pattern=r"^#[0-9A-Fa-f]{6}$")
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_rectangles(self):
        for rect in [self.rect, *(self.rects or [])]:
            if (len(rect) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in rect)
                    or rect[0] >= rect[2] or rect[1] >= rect[3]):
                raise ValueError("批注区域须为页面内有效的归一化矩形")
        if self.rects:
            self.rect = [min(r[0] for r in self.rects), min(r[1] for r in self.rects),
                         max(r[2] for r in self.rects), max(r[3] for r in self.rects)]
        self.color = self.color.lower()
        return self


def _regular(path):
    if path.is_symlink():
        raise AnnotationError("批注文件路径无效", 400)


def _signature(path):
    _regular(path)
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise AnnotationError("PDF 尚未生成", 404) from exc
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _unchanged(path, signature):
    if _signature(path) != signature:
        raise AnnotationError("PDF 正在更新，请稍后重试", 409)


@contextmanager
def _open_snapshot(path, signature):
    """Do not return data or native errors from a concurrently replaced PDF."""
    with PDF_LOCK:
        _unchanged(path, signature)
        try:
            with fitz.open(path) as doc:
                yield doc
        except (fitz.FileDataError, RuntimeError, IndexError) as exc:
            _unchanged(path, signature)
            raise AnnotationError("无法读取 PDF 页面") from exc
        _unchanged(path, signature)


def document_info(path):
    """Hash once per changed file; PDF page access shares the renderer's MuPDF lock."""
    path = Path(path)
    with LOCK:
        signature = _signature(path)
        key = str(path.resolve())
        cached = _DOCUMENTS.get(key)
        if cached and cached[0] == signature:
            _DOCUMENTS.move_to_end(key)
            return copy.deepcopy(cached[1])
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            with PDF_LOCK, fitz.open(path) as doc:
                if doc.needs_pass:
                    raise AnnotationError("请先解锁 PDF 后再添加批注")
                pages = [{"page": p.number + 1, "width": p.rect.width, "height": p.rect.height}
                         for p in doc]
        except (fitz.FileDataError, RuntimeError) as exc:
            raise AnnotationError("无法读取 PDF 页面") from exc
        _unchanged(path, signature)
        result = {"document_revision": digest.hexdigest(), "page_count": len(pages), "pages": pages}
        _DOCUMENTS[key] = signature, result
        while len(_DOCUMENTS) > 128:
            _DOCUMENTS.popitem(last=False)
        return copy.deepcopy(result)


def _load(folder):
    path = Path(folder) / "annotations.json"
    _regular(path)
    if not path.exists():
        return {"schema": 1, "annotations": {"original": [], "translated": []},
                "deleted": {"original": [], "translated": []}}
    try:
        if path.stat().st_size > 24 * 1024 * 1024:
            raise ValueError("too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("annotations"), dict):
            raise ValueError("schema")
        data.setdefault("deleted", {"original": [], "translated": []})
        if not isinstance(data["deleted"], dict):
            raise ValueError("deleted")
        for version in ("original", "translated"):
            records = data["annotations"][version]
            if not isinstance(records, list) or len(records) > MAX_ANNOTATIONS:
                raise ValueError("records")
            for record in records:
                AnnotationCreate.model_validate({key: record[key] for key in AnnotationCreate.model_fields})
                if not isinstance(record["id"], str):
                    raise ValueError("id")
            tombstones = data["deleted"][version]
            if not isinstance(tombstones, list) or len(tombstones) > 100000:
                raise ValueError("deleted")
            for deleted in tombstones:
                if (not isinstance(deleted, dict) or not isinstance(deleted.get("id"), str)
                        or not re.fullmatch(ANNOTATION_ID, deleted["id"])
                        or not isinstance(deleted.get("document_revision"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", deleted["document_revision"])):
                    raise ValueError("deleted id")
        return data
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Never discard existing reader notes when their storage is damaged.
        raise AnnotationError("批注记录无法读取，原记录已保留", 409) from exc


def _save(folder, data):
    path = Path(folder) / "annotations.json"
    _regular(path)
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 24 * 1024 * 1024:
        raise AnnotationError("此论文批注存储已达上限，请先导出并删除部分批注")
    temp = path.with_name(f".annotations-{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _version(version):
    if version not in {"original", "translated"}:
        raise AnnotationError("未知批注版本")


def list_annotations(folder, version):
    _version(version)
    with LOCK:
        path = Path(folder) / f"{version}.pdf"
        signature = _signature(path)
        info = document_info(path)
        records = _load(folder)["annotations"][version]
        current = [r for r in records if r["document_revision"] == info["document_revision"]]
        _unchanged(path, signature)
        return {**info, "version": version, "annotations": copy.deepcopy(current),
                "stale_count": len(records) - len(current)}


def _check_revision(info, revision):
    if info["document_revision"] != revision:
        raise AnnotationError("PDF 已更新，请重新打开当前页后再批注", 409)


def add_annotation(folder, annotation):
    with LOCK:
        path = Path(folder) / f"{annotation.version}.pdf"
        signature = _signature(path)
        info = document_info(path)
        _check_revision(info, annotation.document_revision)
        if annotation.page > info["page_count"]:
            raise AnnotationError("页码超出范围")
        data = _load(folder)
        _unchanged(path, signature)
        records = data["annotations"][annotation.version]
        requested = annotation.model_dump()
        requested["rects"] = annotation.rects or [annotation.rect]
        if annotation.id:
            if any(deleted["id"] == annotation.id and deleted["document_revision"] == annotation.document_revision
                   for deleted in data["deleted"][annotation.version]):
                raise AnnotationError("该批注已删除，请使用新的批注编号", 410)
            for existing in records:
                if existing["id"] == annotation.id:
                    if all(existing[k] == v for k, v in requested.items()):
                        return copy.deepcopy(existing)
                    raise AnnotationError("此批注编号已存在，请刷新页面后重试", 409)
        if len(records) >= MAX_ANNOTATIONS:
            raise AnnotationError("此论文批注数量已达上限，请先导出并删除部分批注")
        record = requested
        record.update(id=annotation.id or uuid.uuid4().hex, created_at=time.time(),
                      rects=annotation.rects or [annotation.rect])
        records.append(record)
        _unchanged(path, signature)
        _save(folder, data)
        return copy.deepcopy(record)


def delete_annotation(folder, version, annotation_id, revision):
    _version(version)
    with LOCK:
        path = Path(folder) / f"{version}.pdf"
        signature = _signature(path)
        info = document_info(path)
        _check_revision(info, revision)
        data = _load(folder)
        _unchanged(path, signature)
        records = data["annotations"][version]
        tombstones = data["deleted"][version]
        if any(deleted["id"] == annotation_id and deleted["document_revision"] == revision for deleted in tombstones):
            return {"deleted": annotation_id}
        if not re.fullmatch(ANNOTATION_ID, annotation_id):
            raise AnnotationError("未找到该批注", 404)
        if len(tombstones) >= 100000:
            raise AnnotationError("此论文的批注操作记录已达存储上限，请先导出批注")
        for index, record in enumerate(records):
            if record["id"] == annotation_id and record["document_revision"] == revision:
                del records[index]
                break
        # Also remember a valid client UUID that has not arrived yet: a delayed
        # POST after cancellation must never bring the deleted mark back.
        tombstones.append({"id": annotation_id, "document_revision": revision, "deleted_at": time.time()})
        _unchanged(path, signature)
        _save(folder, data)
        return {"deleted": annotation_id}


def _normalized_rect(rect, bounds):
    values = [rect.x0 / bounds.width, rect.y0 / bounds.height,
              rect.x1 / bounds.width, rect.y1 / bounds.height]
    return [max(0.0, min(1.0, v)) for v in values]


def _cjk_word_boxes(page):
    """CJK characters are selectable individually; adjacent Latin letters stay words.

    Excluding image bytes is essential on scientific PDFs with many image objects.
    The raw character layout is only requested for pages that actually contain CJK.
    """
    raw = page.get_text("rawdict", flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
    emitted = 0
    inspected = 0
    for block_number, block in enumerate(raw["blocks"]):
        for line_number, line in enumerate(block.get("lines", [])):
            pending = []
            pending_rect = None
            for span in line["spans"]:
                for char in span["chars"]:
                    inspected += 1
                    text, rect = char["c"], fitz.Rect(char["bbox"])
                    if text.isspace() or CJK.search(text):
                        if pending:
                            yield (*pending_rect, "".join(pending), block_number, line_number)
                            emitted += 1
                            pending, pending_rect = [], None
                        if not text.isspace():
                            yield (*rect, text, block_number, line_number)
                            emitted += 1
                    else:
                        pending.append(text)
                        pending_rect = rect if pending_rect is None else pending_rect | rect
                    if emitted > MAX_WORD_BOXES or inspected > 200000:
                        # One sentinel beyond the limit marks a bounded response.
                        yield (0, 0, 0, 0, "", block_number, line_number)
                        return
            if pending:
                yield (*pending_rect, "".join(pending), block_number, line_number)
                emitted += 1


def page_words(folder, version, number):
    _version(version)
    path = Path(folder) / f"{version}.pdf"
    with LOCK:
        signature = _signature(path)
        info = document_info(path)
        _unchanged(path, signature)
        if not 1 <= number <= info["page_count"]:
            raise AnnotationError("页码超出范围")
        with _open_snapshot(path, signature) as doc:
            page = doc[number - 1]
            groups = OrderedDict()
            words = page.get_text("words", sort=True)
            boxes = _cjk_word_boxes(page) if any(CJK.search(word[4]) for word in words) else iter(words)
            truncated = False
            for index, word in enumerate(boxes):
                if index >= MAX_WORD_BOXES or not word[4]:
                    truncated = True
                    break
                rect = _normalized_rect(fitz.Rect(word[:4]) * page.rotation_matrix, page.rect)
                if rect[0] >= rect[2] or rect[1] >= rect[3]:
                    continue
                groups.setdefault((word[5], word[6]), []).append({"rect": rect, "text": word[4]})
            lines = [{"rect": [min(w["rect"][0] for w in ws), min(w["rect"][1] for w in ws),
                                max(w["rect"][2] for w in ws), max(w["rect"][3] for w in ws)],
                      "words": ws} for ws in groups.values()]
        _unchanged(path, signature)
        return {"document_revision": info["document_revision"], "page": number,
                "lines": lines, "truncated": truncated}


def highlight_opacity(color):
    """Match reader colors while preserving dark text under custom dark colors."""
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]

    def luminance(alpha):
        channels = [1 - alpha + alpha * channel for channel in rgb]
        return sum(weight * (value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
                   for weight, value in zip((0.2126, 0.7152, 0.0722), channels))

    low, high = 0.0, 0.68
    if luminance(high) >= 0.30:
        return high
    for _ in range(24):
        mid = (low + high) / 2
        if luminance(mid) >= 0.30:
            low = mid
        else:
            high = mid
    return math.floor(low * 1000) / 1000


def export_annotations(folder, version, revision=None):
    """Return a new PDF with editable native markup; never rewrite either source PDF."""
    _version(version)
    path = Path(folder) / f"{version}.pdf"
    with LOCK:
        signature = _signature(path)
        payload = list_annotations(folder, version)
        _unchanged(path, signature)
        if revision is not None:
            _check_revision(payload, revision)
        with _open_snapshot(path, signature) as doc:
            for record in payload["annotations"]:
                page = doc[record["page"] - 1]
                quads = []
                for r in record["rects"]:
                    display = fitz.Rect(r[0] * page.rect.width, r[1] * page.rect.height,
                                        r[2] * page.rect.width, r[3] * page.rect.height)
                    # Transform each ordered corner, not just the bounding rectangle,
                    # so underlines stay on the displayed bottom edge at any rotation.
                    quads.append(fitz.Quad([p * page.derotation_matrix for p in
                                           (display.tl, display.tr, display.bl, display.br)]))
                annot = (page.add_highlight_annot(quads) if record["kind"] == "highlight"
                         else page.add_underline_annot(quads))
                rgb = tuple(int(record["color"][i:i + 2], 16) / 255 for i in (1, 3, 5))
                annot.set_colors(stroke=rgb)
                annot.set_opacity(highlight_opacity(record["color"]) if record["kind"] == "highlight" else 1)
                annot.set_info(title="译研批注", content=record["note"], subject=record["id"])
                annot.set_flags(fitz.PDF_ANNOT_IS_PRINT)
                annot.update()
            result = doc.tobytes(deflate=True, garbage=1)
        _unchanged(path, signature)
        return result
