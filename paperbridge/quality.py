"""Independent PDF checks. Mechanical evidence never implies semantic accuracy."""
import hashlib
import math
import re
import time
from collections import Counter

import pymupdf as fitz
import numpy as np

MATH_FONT = re.compile(r"symbol|math|cmsy|cmmi|cmex|msam|msbm", re.I)
RENDER_SCALE = 1.5
MAX_RENDER_PIXELS = 4_000_000
TILE_EDGE = 1024


def _pixel_differences(first, second):
    """Keep only one byte per pixel after comparing both lossless RGB renders."""
    if first.irect != second.irect or first.n != second.n:
        return None
    shape = (first.height, first.width, first.n)
    a = np.frombuffer(first.samples_mv, dtype=np.uint8).reshape(shape)
    b = np.frombuffer(second.samples_mv, dtype=np.uint8).reshape(shape)
    # Subtract in sorted order to avoid uint8 wraparound, without int16 copies.
    return (np.maximum(a, b) - np.minimum(a, b)).max(axis=2)


def _image_differences(a, b, images, notify, check_cancel, counters):
    matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
    bounds = (a.rect * matrix).irect
    rectangles = []
    for index, image in enumerate(images):
        if index % 256 == 0:
            check_cancel()
        # Image bboxes are unrotated; page/display-list coordinates are rotated.
        rectangle = (fitz.Rect(image['bbox']) * a.rotation_matrix) & a.rect
        if not rectangle.is_empty:
            pixels = (rectangle * matrix).irect & bounds
            if not pixels.is_empty:
                rectangles.append(tuple(pixels))
    if not rectangles:
        return []
    if a.rect != b.rect or a.rotation != b.rotation:
        return [255] * len(rectangles)
    # Parse each page once. A clipped Page.get_pixmap reparses its entire content
    # stream, even if the requested image covers less than a single pixel.
    left, right = a.get_displaylist(), b.get_displaylist()
    counters['display_lists'] += 2
    maxima = dict.fromkeys(rectangles, 0)
    if bounds.width * bounds.height <= MAX_RENDER_PIXELS:
        delta = _pixel_differences(left.get_pixmap(matrix=matrix, alpha=False),
                                   right.get_pixmap(matrix=matrix, alpha=False))
        counters['page_renders'] += 2
        for index, rectangle in enumerate(maxima):
            x0, y0, x1, y1 = rectangle
            maxima[rectangle] = 255 if delta is None else int(delta[y0-bounds.y0:y1-bounds.y0, x0-bounds.x0:x1-bounds.x0].max(initial=0))
            if index % 128 == 0:
                check_cancel()
                notify((index + 1) / len(maxima), index + 1, len(maxima), 'image_regions')
        del delta
    else:
        # Oversized pages use bounded tiles. Each tile is rendered at most once
        # per document, regardless of how many overlapping images it contains.
        tiles = [(x, y) for y in range(bounds.y0, bounds.y1, TILE_EDGE)
                 for x in range(bounds.x0, bounds.x1, TILE_EDGE)]
        for index, (x, y) in enumerate(tiles):
            check_cancel()
            tile = fitz.IRect(x, y, min(x + TILE_EDGE, bounds.x1), min(y + TILE_EDGE, bounds.y1))
            regions = [(key, fitz.IRect(key) & tile) for key in maxima
                       if key[0] < tile.x1 and key[2] > tile.x0 and key[1] < tile.y1 and key[3] > tile.y0]
            if regions:
                clip = fitz.Rect(tile) / matrix
                first = left.get_pixmap(matrix=matrix, clip=clip, alpha=False)
                second = right.get_pixmap(matrix=matrix, clip=clip, alpha=False)
                delta = _pixel_differences(first, second)
                counters['tile_renders'] += 2
                for key, region in regions:
                    value = 255 if delta is None else int(delta[region.y0-first.y:region.y1-first.y, region.x0-first.x:region.x1-first.x].max(initial=0))
                    maxima[key] = max(maxima[key], value)
                del first, second, delta
            notify((index + 1) / len(tiles), index + 1, len(tiles), 'image_tiles')
    check_cancel()
    notify(1, len(maxima), len(maxima), 'image_regions')
    counters['image_regions_checked'] += len(rectangles)
    counters['unique_pixel_regions'] += len(maxima)
    return [maxima[key] for key in rectangles]


def _font(name):
    return re.sub(r"^[A-Z]{6}\+", "", name)


def math_glyphs(page):
    result = []
    # ActualText can contain a whole TeX source string for a single painted
    # equation glyph. Compare visible glyphs, not that accessibility annotation.
    flags = (fitz.TEXTFLAGS_RAWDICT | fitz.TEXT_IGNORE_ACTUALTEXT) & ~fitz.TEXT_PRESERVE_IMAGES
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if MATH_FONT.search(span["font"]):
                    for char in span["chars"]:
                        if char["c"].strip():
                            result.append((_font(span["font"]), char["c"], char["origin"]))
    return result


def inspect_pdf(source, target, segments, *, progress=None, cancelled=None):
    started = time.monotonic()
    counters = {'display_lists': 0, 'page_renders': 0, 'tile_renders': 0,
                'image_regions_checked': 0, 'unique_pixel_regions': 0}
    def check_cancel():
        if cancelled and cancelled():
            raise InterruptedError('已停止 PDF 校验，已完成译文保留')
    last_event = [None, 0.0]
    def notify(page, pages, phase, fraction, completed=0, total=0, unit='pages'):
        check_cancel()
        now = time.monotonic()
        key = (page, phase)
        if progress and (key != last_event[0] or fraction == 1 or now - last_event[1] >= .25):
            progress({'page': page, 'pages': pages, 'phase': phase,
                      'fraction': ((page - 1) + fraction) / max(1, pages),
                      'completed': completed, 'total': total, 'unit': unit})
            last_event[:] = [key, now]
    check_cancel()
    report = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "semantic_accuracy": "not_proven_by_mechanical_checks",
              "math_text_policy": "visible_glyphs_ignore_actualtext", "pages": []}
    with fitz.open(source) as original, fitz.open(target) as translated:
        report["page_count_preserved"] = len(original) == len(translated)
        if len(original) != len(translated):
            report["structural_checks_passed"] = False
            return report
        for index, (a, b) in enumerate(zip(original, translated)):
            page_number, pages = index + 1, len(original)
            notify(page_number, pages, 'image_inventory', 0)
            source_images = a.get_image_info(hashes=True)
            check_cancel()
            target_images = b.get_image_info(hashes=True)
            def images(inventory):
                return sorted((tuple(round(x, 3) for x in i["bbox"]), i["digest"].hex())
                              for i in inventory)
            notify(page_number, pages, 'images', .1, 0, len(source_images), 'image_regions')
            image_differences = _image_differences(a, b, source_images,
                lambda fraction, done, total, unit: notify(page_number, pages, 'images', .1 + .65 * fraction, done, total, unit),
                check_cancel, counters)
            notify(page_number, pages, 'vectors', .78)
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
            notify(page_number, pages, 'formulae', .9)
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
                                    "images_preserved": images(source_images) == images(target_images),
                                    "image_appearance_preserved": all(delta <= 2 for delta in image_differences),
                                    "image_region_max_pixel_deltas": image_differences,
                                    "vector_primitives_preserved": vector_ok,
                                    "math_symbol_inventory_preserved": not bool(ca - cb),
                                    "missing_math_symbols": dict(ca - cb),
                                    "fixed_math_glyph_count": len(fixed), "fixed_math_positions_preserved": not lost_fixed,
                                    "missing_fixed_math_glyphs": [{"symbol": g[1], "origin": g[2]} for g in lost_fixed]})
            notify(page_number, pages, 'page_complete', 1, page_number, pages)
        keys = ["geometry_preserved", "images_preserved", "image_appearance_preserved", "vector_primitives_preserved", "math_symbol_inventory_preserved", "fixed_math_positions_preserved"]
        report["structural_checks_passed"] = all(all(p[k] for k in keys) for p in report["pages"])
        report["failed_pages"] = [p["page"] for p in report["pages"] if not all(p[k] for k in keys)]
        report["method"] = "页数与尺寸、嵌入图像像素摘要和坐标、图像区域实际渲染（RGB最大差值不超过2/255）、自然语言段落之外的矢量图元（0.001pt）、数学字体字母与运算符多重集、自然语言段落之外的数学字形位置（0.1pt）；行内公式允许随中文重排"
        report['performance'] = {**counters, 'elapsed_seconds': round(time.monotonic() - started, 3),
                                 'image_rendering': 'shared_page_rgb_or_bounded_tiles',
                                 'render_scale': RENDER_SCALE, 'maximum_full_page_pixels': MAX_RENDER_PIXELS}
    return report
