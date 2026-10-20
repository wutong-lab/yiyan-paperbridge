"""Conservative text replacement on the original PDF object structure."""
import hashlib
import html
import re
import threading
from pathlib import Path

import pymupdf as fitz

from .sections import detect_sections, is_reference_region

# MuPDF is not thread safe, even for different documents. Serialize all access.
PDF_LOCK = threading.RLock()


def _union(rectangles):
    result = fitz.Rect(rectangles[0])
    for rectangle in rectangles[1:]:
        result |= fitz.Rect(rectangle)
    return result


def _inside(rect, region):
    rect, region = fitz.Rect(rect), fitz.Rect(region)
    center = (rect.tl + rect.br) / 2
    return region.contains(center)


def _overlap(rect, region):
    rect = fitz.Rect(rect)
    return (rect & fitz.Rect(region)).get_area() / max(1, rect.get_area())


def _text(lines):
    result = "\n".join("".join(s["text"] for s in line["spans"]) for line in lines)
    result = re.sub(r"([A-Za-z])[-‐]\n([a-z])", r"\1\2", result)
    return re.sub(r"\s+", " ", result).strip()


def _protected(line):
    text = _text([line])
    if abs(line.get("dir", (1, 0))[1]) > 0.01:
        return "旋转文字保留原文"
    if not re.search(r"[A-Za-z]{2}", text):
        return "数值、符号或页码保留原文"
    if any(re.search(r"symbol|math|cmsy|cmmi|cmex|msam|msbm", s.get("font", ""), re.I) for s in line["spans"]):
        return "含公式字形，保留原文"
    if any(s.get("flags", 0) & 1 for s in line["spans"]):
        return "含上标或脚注标记，保留原文"
    if re.search(r"[=∫∑√≠≤≥∂∞]", text):
        return "含数学表达式，保留原文"
    if re.search(r"https?://|\bdoi\b|@", text, re.I):
        return "网址、DOI 或邮箱保留原文"
    return ""


def _segment(lines, page_number, index, kind="text", rect=None, reason=""):
    spans = [s for line in lines for s in line["spans"] if s["text"].strip()]
    if not spans:
        return None
    value = _text(lines)
    if not value:
        return None
    font_size = sorted(s["size"] for s in spans)[len(spans) // 2]
    return {
        "id": f"p{page_number + 1}-b{index}", "page": page_number + 1,
        "source": value, "translation": "", "bbox": list(rect or _union([l["bbox"] for l in lines])),
        "line_boxes": [list(l["bbox"]) for l in lines], "font_size": font_size,
        "bold": any(s.get("flags", 0) & 16 for s in spans),
        "color": spans[0].get("color", 0), "kind": kind,
        "status": "preserved" if reason else "pending", "reason": reason,
        "notes": [], "exported": False,
    }


def analyze(path: Path) -> dict:
    with PDF_LOCK, fitz.open(path) as document:
        if document.needs_pass:
            raise ValueError("PDF 已加密，请先用密码解锁后导入")
        if not document.is_pdf or not 0 < len(document) <= 300:
            raise ValueError("请导入 1 至 300 页的 PDF")
        segments, pages, warnings = [], [], []
        sections = detect_sections(path)
        for page_number, page in enumerate(document):
            if page.rect.width > 5000 or page.rect.height > 5000:
                raise ValueError("页面尺寸过大，暂不支持此 PDF")
            blocks = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
            images = page.get_image_info()
            image_rects = [i["bbox"] for i in images]
            tables = []
            try:
                for table in page.find_tables().tables:
                    if table.row_count >= 2 and table.col_count >= 2:
                        tables.append({"bbox": list(table.bbox), "cells": [list(c) for c in table.cells if c]})
            except Exception:
                warnings.append({"page": page_number + 1, "message": "表格检测未完成，请核对本页表格"})
            drawings = page.get_drawings()
            graphic_rects = []
            try:
                for cluster in page.cluster_drawings(drawings=drawings):
                    if cluster.width > 40 and cluster.height > 25 and cluster.get_area() < page.rect.get_area() * .5:
                        if not any(_overlap(cluster, t["bbox"]) > .5 for t in tables):
                            graphic_rects.append(cluster)
            except Exception:
                warnings.append({"page": page_number + 1, "message": "图内文字检测未完成，请核对本页图表"})
            page_segments = []
            has_redactions = any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in (page.annots() or []))
            if has_redactions:
                warnings.append({"page": page_number + 1, "message": "页面含原有涂黑标注，保留原页以避免意外删除内容"})
            table_lines = {tuple(cell): [] for table in tables for cell in table["cells"]}
            for block in blocks:
                if block.get("type") != 0:
                    continue
                buffer = []
                def flush(reason=""):
                    if buffer:
                        seg = _segment(buffer, page_number, len(page_segments), reason=reason)
                        if seg:
                            page_segments.append(seg)
                        buffer.clear()
                for line in block["lines"]:
                    value = _text([line])
                    if not value:
                        continue
                    cell = next((c for c in table_lines if _inside(line["bbox"], c)), None)
                    references = is_reference_region(page_number + 1, line["bbox"], sections["reference_regions"])
                    if cell:
                        flush()
                        line["in_references"] = references
                        table_lines[cell].append(line)
                        continue
                    reason = _protected(line)
                    if references:
                        reason = "参考文献保留原文"
                    elif any(_overlap(line["bbox"], r) > .25 for r in image_rects + graphic_rects):
                        reason = "图内或图片上的文字保留原文"
                    elif line["bbox"][1] < 28 or line["bbox"][3] > page.cropbox.height - 25:
                        reason = "页眉或页脚保留原文"
                    elif len(value) > 1800:
                        reason = "超长文字区域，待人工拆分"
                    if reason:
                        flush()
                        seg = _segment([line], page_number, len(page_segments), reason=reason)
                        if seg:
                            page_segments.append(seg)
                    else:
                        # Avoid exceeding the local model context or joining separate columns.
                        if buffer and (len(_text(buffer)) + len(value) > 1500 or abs(line["bbox"][0] - buffer[-1]["bbox"][0]) > 55):
                            flush()
                        buffer.append(line)
                flush()
            for cell, lines in table_lines.items():
                if not lines:
                    continue
                reason = next((_protected(l) for l in lines if _protected(l)), "")
                if not all(_overlap(l["bbox"], cell) > .9 for l in lines):
                    reason = "跨单元格文字保留原文"
                if any(l.get("in_references") for l in lines):
                    reason = "参考文献保留原文"
                seg = _segment(lines, page_number, len(page_segments), kind="table", rect=fitz.Rect(cell) + (2, 1, -2, -1), reason=reason)
                if seg:
                    page_segments.append(seg)
            # A bounding box must not cover any other text, including protected formulas.
            for seg in page_segments:
                if has_redactions:
                    seg["status"], seg["reason"] = "preserved", "原有涂黑标注，保护整页"
                if seg["status"] != "pending":
                    continue
                if any(other["id"] != seg["id"] and any(_overlap(box, seg["bbox"]) > .08 for box in other["line_boxes"]) for other in page_segments):
                    seg["status"] = "preserved"
                    seg["reason"] = "文字区域重叠，保留原文待核对"
            page_segments.sort(key=lambda s: (round(s["bbox"][0] / 70), s["bbox"][1]))
            characters = sum(len(s["source"]) for s in page_segments)
            scanned = characters < 30 and bool(images)
            if scanned:
                warnings.append({"page": page_number + 1, "message": "疑似扫描页，目前保留原页；需要 OCR 后才能翻译"})
            if not page_segments and not scanned:
                warnings.append({"page": page_number + 1, "message": "没有可提取文字，保留原页"})
            segments.extend(page_segments)
            pages.append({"number": page_number + 1, "width": page.rect.width, "height": page.rect.height,
                          "images": len(images), "tables": len(tables), "scanned": scanned,
                          "table_regions": [table["bbox"] for table in tables],
                          "blocks": len(page_segments), "candidates": sum(s["status"] == "pending" for s in page_segments)})
        return {"pages": pages, "segments": segments, "warnings": warnings,
                "page_count": len(document), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "metadata": document.metadata, "source_sections": sections}


def _content(segment):
    text = html.escape(segment["translation"])
    weight = "bold" if segment["bold"] else "normal"
    size = max(8, min(24, segment["font_size"] * .95))
    color = f"#{segment['color']:06x}"
    css = f"* {{margin:0;padding:0}} body {{font-family:sans-serif;font-size:{size}pt;line-height:1.12;color:{color};font-weight:{weight};}}"
    return f"<div>{text}</div>", css, size


def export_pdf(source: Path, target: Path, dual: Path, segments: list) -> dict:
    """Probe before redacting. A fit failure must leave original text intact."""
    with PDF_LOCK, fitz.open(source) as doc:
        report = {"replaced": 0, "overflow": [], "protected_overlap": [], "preserved": 0, "page_count": len(doc)}
        for page_number, page in enumerate(doc):
            accepted = []
            original_links = page.get_links()
            page_segments = [s for s in segments if s["page"] == page_number + 1]
            for seg in page_segments:
                seg["exported"] = False
                if not seg["translation"] or seg["status"] not in {"translated", "review", "edited"}:
                    report["preserved"] += 1
                    continue
                # MuPDF removes a glyph on any positive intersection with a redaction.
                # A percentage threshold is unsafe for a tall math glyph on an adjacent line.
                if any((fitz.Rect(box) & fitz.Rect(other_box)).get_area() > .001
                       for other in page_segments if other["id"] != seg["id"]
                       for box in seg["line_boxes"] for other_box in other["line_boxes"]):
                    report["protected_overlap"].append(seg["id"])
                    seg["notes"] = [n for n in seg["notes"] if not n.startswith("排版：")]
                    seg["notes"].append("排版：替换边界与邻近文字或公式相交，PDF 中保留英文；完整中文可在对照中阅读")
                    seg["status"] = "review"
                    continue
                rect = fitz.Rect(seg["bbox"])
                content, css, size = _content(seg)
                scale_low = min(1, max(.68, 7 / size))
                with fitz.open() as probe:
                    test_page = probe.new_page(width=max(100, rect.x1 + 10), height=max(100, rect.y1 + 10))
                    spare, scale = test_page.insert_htmlbox(rect, content, css=css, scale_low=scale_low)
                if spare < 0:
                    report["overflow"].append(seg["id"])
                    seg["notes"] = [n for n in seg["notes"] if not n.startswith("排版：")]
                    seg["notes"].append("排版：译文超出原区域，PDF 中保留英文；完整中文可在对照中阅读")
                    seg["status"] = "review"
                    continue
                accepted.append((seg, rect, content, css, scale_low))
                for box in seg["line_boxes"]:
                    # Exact line boxes; transparent redactions cannot cover drawings.
                    page.add_redact_annot(fitz.Rect(box), fill=False, cross_out=False)
            if accepted:
                # Preserve original image objects and vector paths (including table grid).
                page.apply_redactions(images=0, graphics=0, text=0)
                for seg, rect, content, css, scale_low in accepted:
                    spare, scale = page.insert_htmlbox(rect, content, css=css, scale_low=scale_low)
                    if spare < 0:
                        raise RuntimeError("排版探测与写入结果不一致，未发布此导出")
                    seg["exported"] = True
                    seg["render_font_size"] = round(_content(seg)[2] * scale, 2)
                    report["replaced"] += 1
                surviving = page.get_links()
                for link in original_links:
                    if not any(existing["from"] == link["from"] and existing.get("uri") == link.get("uri") and existing.get("page") == link.get("page") for existing in surviving):
                        restored = {k: v for k, v in link.items() if k not in {"xref", "id"}}
                        restored["from"] = link["from"] * page.derotation_matrix
                        page.insert_link(restored)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part.pdf")
        doc.save(temporary, garbage=4, deflate=True)
        with fitz.open(temporary) as check:
            if len(check) != len(doc) or any(check[i].rect != doc[i].rect for i in range(len(doc))):
                raise RuntimeError("输出页数或尺寸验证失败")
        from .pdf_compaction import compact_pdf
        report["pdf_compression"] = compact_pdf(temporary)
        temporary.replace(target)
    with PDF_LOCK, fitz.open(source) as original, fitz.open(target) as translated, fitz.open() as bilingual:
        for i in range(len(original)):
            final = i == len(original) - 1
            bilingual.insert_pdf(original, from_page=i, to_page=i, final=final)
            bilingual.insert_pdf(translated, from_page=i, to_page=i, final=final)
        temp_dual = dual.with_suffix(".part.pdf")
        bilingual.save(temp_dual, garbage=4, deflate=True)
        with fitz.open(temp_dual) as check:
            if len(check) != 2 * len(original) or any(
                    check[2*i].rect != original[i].rect or check[2*i+1].rect != translated[i].rect
                    for i in range(len(original))):
                raise RuntimeError("双语输出页数或尺寸验证失败")
        temp_dual.replace(dual)
    report["structural_checks_passed"] = True
    report["structural_check_scope"] = "中文与双语的页数、页面尺寸"
    return report


def render_page(path: Path, number: int, width: int = 1100) -> bytes:
    with PDF_LOCK, fitz.open(path) as doc:
        if not 1 <= number <= len(doc):
            raise ValueError("页码超出范围")
        page = doc[number - 1]
        scale = min(width / page.rect.width, 4400 / max(page.rect.width, page.rect.height))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
