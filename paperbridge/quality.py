"""Independent PDF checks. Mechanical evidence never implies semantic accuracy."""
import hashlib
import math
import re
from collections import Counter

import pymupdf as fitz
import numpy as np

MATH_FONT = re.compile(r"symbol|math|cmsy|cmmi|cmex|msam|msbm", re.I)


def _font(name):
    return re.sub(r"^[A-Z]{6}\+", "", name)


def math_glyphs(page):
    result = []
    # ActualText can contain a whole TeX source string for a single painted
    # equation glyph. Compare visible glyphs, not that accessibility annotation.
    flags = fitz.TEXTFLAGS_RAWDICT | fitz.TEXT_IGNORE_ACTUALTEXT
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if MATH_FONT.search(span["font"]):
                    for char in span["chars"]:
                        if char["c"].strip():
                            result.append((_font(span["font"]), char["c"], char["origin"]))
    return result


def inspect_pdf(source, target, segments):
    report = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "semantic_accuracy": "not_proven_by_mechanical_checks",
              "math_text_policy": "visible_glyphs_ignore_actualtext", "pages": []}
    with fitz.open(source) as original, fitz.open(target) as translated:
        report["page_count_preserved"] = len(original) == len(translated)
        if len(original) != len(translated):
            report["structural_checks_passed"] = False
            return report
        for index, (a, b) in enumerate(zip(original, translated)):
            def images(page):
                return sorted((tuple(round(x, 3) for x in i["bbox"]), i["digest"].hex())
                              for i in page.get_image_info(hashes=True))
            image_differences = []
            for image in a.get_image_info():
                rectangle = fitz.Rect(image["bbox"]) & a.rect
                if rectangle.is_empty:
                    continue
                source_clip = a.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=rectangle, alpha=False)
                target_clip = b.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=rectangle, alpha=False)
                if (source_clip.width, source_clip.height, source_clip.n) != (target_clip.width, target_clip.height, target_clip.n):
                    delta = 255
                else:
                    delta = int(np.max(np.abs(np.frombuffer(source_clip.samples, dtype=np.uint8).astype(np.int16)
                                               - np.frombuffer(target_clip.samples, dtype=np.uint8).astype(np.int16))))
                image_differences.append(delta)
            natural_boxes = [fitz.Rect(s["bbox"]) + (-2, -2, 2, 2) for s in segments if s["page"] == index + 1
                             and s.get("translation") and s.get("exported")]
            def paths(page):
                # The PDF engine can change path grouping; compare each vector
                # primitive independently, with 0.001 pt rounding tolerance.
                items = []
                def shape(part):
                    if isinstance(part, (fitz.Point, fitz.Rect, fitz.Quad, tuple, list)):
                        return tuple(shape(x) for x in part)
                    return part
                for drawing in page.get_drawings():
                    for item in drawing["items"]:
                        points = [fitz.Point(p) for p in item[1:] if isinstance(p, fitz.Point)]
                        if points and any(all(box.contains(point) for point in points) for box in natural_boxes):
                            continue
                        coords = tuple(shape(part) for part in item[1:])
                        items.append((item[0], coords))
                return items
            def near(x, y):
                if isinstance(x, (tuple, list)) and isinstance(y, (tuple, list)):
                    return len(x) == len(y) and all(near(u, v) for u, v in zip(x, y))
                if isinstance(x, (float, int)) and isinstance(y, (float, int)):
                    return abs(x - y) < .001
                return x == y
            def vector_match(left, right):
                if len(left) != len(right):
                    return False
                buckets = {}
                def bucket(item):
                    def coordinates(value):
                        if isinstance(value, (tuple, list)):
                            for child in value:
                                yield from coordinates(child)
                        elif isinstance(value, (float, int)):
                            yield value
                    values = iter(coordinates(item[1]))
                    # Bin both coordinates of the first point at the existing
                    # 0.001 pt tolerance. Dense plots otherwise put thousands
                    # of unrelated paths in one broad x-only bucket.
                    return item[0], math.floor(next(values) / .001), math.floor(next(values) / .001)
                for item in right:
                    buckets.setdefault(bucket(item), []).append(item)
                for item in left:
                    kind, x, y = bucket(item)
                    found = False
                    for dx, dy in [(a, b) for a in [0, -1, 1] for b in [0, -1, 1]]:
                        candidates = buckets.get((kind, x + dx, y + dy), [])
                        def distance(x, y):
                            if isinstance(x, (tuple, list)) and isinstance(y, (tuple, list)):
                                if len(x) != len(y):
                                    return float("inf")
                                return max((distance(u, v) for u, v in zip(x, y)), default=0)
                            if isinstance(x, (float, int)) and isinstance(y, (float, int)):
                                return abs(x - y)
                            return 0 if x == y else float("inf")
                        matches = [(distance(item, candidate), i) for i, candidate in enumerate(candidates)]
                        match = min(matches)[1] if matches and min(matches)[0] < .001 else None
                        if match is not None:
                            candidates.pop(match)
                            found = True
                            break
                    if not found:
                        return False
                return True
            vector_ok = vector_match(paths(a), paths(b))
            ga, gb = math_glyphs(a), math_glyphs(b)
            # Commas/periods may be restored in another font. Inspect meaningful
            # mathematical letters and operators independently of font identity.
            relevant = lambda glyph: glyph[1] not in {",", ".", ";", ":", "(", ")"}
            ca = Counter(g[1] for g in ga if relevant(g))
            cb = Counter(g[1] for g in gb if relevant(g))
            fixed = [g for g in ga if relevant(g) and not any(box.contains(fitz.Point(g[2])) for box in natural_boxes)]
            lost_fixed = [g for g in fixed if not any(g[1] == h[1] and abs(g[2][0] - h[2][0]) < .1
                                                    and abs(g[2][1] - h[2][1]) < .1 for h in gb)]
            report["pages"].append({"page": index + 1,
                                    "geometry_preserved": a.rect == b.rect and a.rotation == b.rotation,
                                    "images_preserved": images(a) == images(b),
                                    "image_appearance_preserved": all(delta <= 2 for delta in image_differences),
                                    "image_region_max_pixel_deltas": image_differences,
                                    "vector_primitives_preserved": vector_ok,
                                    "math_symbol_inventory_preserved": not bool(ca - cb),
                                    "missing_math_symbols": dict(ca - cb),
                                    "fixed_math_glyph_count": len(fixed), "fixed_math_positions_preserved": not lost_fixed,
                                    "missing_fixed_math_glyphs": [{"symbol": g[1], "origin": g[2]} for g in lost_fixed]})
        keys = ["geometry_preserved", "images_preserved", "image_appearance_preserved", "vector_primitives_preserved", "math_symbol_inventory_preserved", "fixed_math_positions_preserved"]
        report["structural_checks_passed"] = all(all(p[k] for k in keys) for p in report["pages"])
        report["failed_pages"] = [p["page"] for p in report["pages"] if not all(p[k] for k in keys)]
        report["method"] = "页数与尺寸、嵌入图像像素摘要和坐标、图像区域实际渲染（RGB最大差值不超过2/255）、自然语言段落之外的矢量图元（0.001pt）、数学字体字母与运算符多重集、自然语言段落之外的数学字形位置（0.1pt）；行内公式允许随中文重排"
    return report
