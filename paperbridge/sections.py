"""Column-aware bibliography regions, derived from the source PDF geometry."""
import re
from statistics import median

import pymupdf as fitz

REFERENCE_TITLE = re.compile(
    r"(?:(?:\d+(?:\.\d+)*[.)]?\s*|[IVXLCDM]+[.)]?\s+))?"
    r"(?:references|bibliography|literature cited)\s*[:.]?", re.I)
APPENDIX_TITLE = re.compile(r"(?:appendix|appendices|supplementary material)(?:\s|[.:]).*|(?:appendix|appendices|supplementary material)", re.I)
CAPTION_START = re.compile(r"^(?:fig(?:ure)?s?\.?|table)\s+(?:[A-Z]\.)?\d+(?:\.\d+)*\s*[:.]\s", re.I)


def block_text(block):
    return " ".join("".join(s["text"] for s in line["spans"]).strip()
                    for line in block.get("lines", []))


def bibliography_block(text):
    # Author-year, numbered IEEE and Vancouver-style reference starts.
    text = re.sub(r"^(?:\[\d+\]|\d+[.)])\s*", "", text.strip())
    name = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ’'-]+"
    return bool(re.match(rf"(?:(?:[A-Z]\.\s*){{1,3}}{name}|{name}\s*,\s*[A-Z]\.|(?:arXiv|https?://|doi:))", text)
                and re.search(r"\b(?:19|20)\d{2}[a-z]?\b|arXiv:|doi:", text))


def column_bounds(page, blocks):
    width = page.rect.width
    midpoint = width / 2
    left, right = [], []
    left_bibliography = right_bibliography = False
    for block in blocks:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"])
            if len(re.findall(r"[A-Za-z]{2,}", text)) < 3:
                continue
            x0, _, x1, _ = line["bbox"]
            if x0 < midpoint - 20 and x1 < midpoint + .03 * width:
                left.append((x0, x1))
                left_bibliography |= bibliography_block(text)
            elif x0 > midpoint - .03 * width and x1 > midpoint + 20:
                right.append((x0, x1))
                right_bibliography |= bibliography_block(text)
    if ((len(left) >= 3 and len(right) >= 3)
            or (len(left) >= 2 and len(right) >= 2 and left_bibliography and right_bibliography)):
        left_edge, right_edge = max(x[1] for x in left), min(x[0] for x in right)
        # Short lines must not pull the column divider away from the actual
        # centre gutter and turn a centred, full-width heading into a side title.
        gutter = midpoint if left_edge < midpoint < right_edge else (left_edge + right_edge) / 2
        return [(0, gutter), (gutter, width)]
    return [(0, width)]


def column_text_blocks(blocks, columns):
    """Split extractor blocks by column while retaining their native lines.

    PDF text blocks are a storage detail: they may contain a heading, paragraphs,
    or both columns. Do not use a combined block's centre as its reading column.
    A line crossing the gutter is kept separately as a spanning line.
    """
    result = []
    for block in blocks:
        grouped = {}
        for line in block.get('lines', []):
            box = line['bbox']
            center = (box[0] + box[2]) / 2
            col = next((i for i, (left, right) in enumerate(columns) if left <= center <= right), 0)
            if len(columns) > 1 and box[0] < columns[0][1] < box[2]:
                col = -1
            grouped.setdefault(col, []).append(line)
        for col, lines in grouped.items():
            lines = sorted(lines, key=lambda line: (line['bbox'][1], line['bbox'][0]))
            box = fitz.Rect(lines[0]['bbox'])
            for line in lines[1:]:
                box |= fitz.Rect(line['bbox'])
            result.append({'lines': lines, 'bbox': list(box), 'column': col})
    return result


def _heading_style(line, neighbours):
    def size(item):
        return max((s['size'] for s in item['spans'] if s['text'].strip()), default=10)

    def bold(item):
        return any(s.get('flags', 0) & 16 or re.search(r'bold|medi|demi', s.get('font', ''), re.I)
                   for s in item['spans'] if s['text'].strip())

    return bool(neighbours and (size(line) > median(size(n) for n in neighbours) * 1.08
                               or bold(line) and not any(bold(n) for n in neighbours)))


def _paragraph_gap(lines, index):
    size = max((s['size'] for s in lines[index]['spans']), default=10)
    return index > 0 and lines[index]['bbox'][1] - lines[index - 1]['bbox'][3] > .2 * size


def _heading_events(blocks):
    events = []
    for block in blocks:
        lines = block['lines']
        for index, line in enumerate(lines):
            text = block_text({'lines': [line]}).strip()
            kind = ('references' if REFERENCE_TITLE.fullmatch(text) else
                    'appendix' if APPENDIX_TITLE.fullmatch(text) else None)
            if not kind or line['bbox'][1] < 25 or abs(line.get('dir', (1, 0))[1]) > .01:
                continue
            # An ordinary sentence mentioning an appendix is not a heading.
            if kind == 'appendix' and re.search(r'[.!?;]$', text) and not re.fullmatch(
                    r'appendi(?:x|ces)(?:\s+[A-Z0-9]+)?[.:]?', text, re.I):
                continue
            if len(lines) > 1:
                neighbours = lines[max(0, index - 1):index] + lines[index + 1:index + 3]
                following = block_text({'lines': lines[index + 1:index + 9]}).strip()
                explicit_appendix = kind == 'appendix' and re.match(
                    r'^(?:appendix|appendices|supplementary material)(?:\s+[A-Z0-9]+)?(?:\s*[.:]|$)', text, re.I)
                if not (_heading_style(line, neighbours) or kind == 'references' and bibliography_block(following)
                        or explicit_appendix and _paragraph_gap(lines, index)):
                    continue
            size = max((s['size'] for s in line['spans']), default=10)
            y = line['bbox'][1]
            events.append({'column': max(0, block['column']), 'y': y, 'kind': kind, 'text': text,
                           'safe_top': y - .4 * size if kind == 'appendix' else y,
                           'spanning': block['column'] == -1})
        # A wrapped title such as "Literature\ncited" still has to match the
        # complete title; never match a keyword inside a sentence or paper title.
        if len(lines) > 1 and REFERENCE_TITLE.fullmatch(block_text(block).strip()):
            y = block['bbox'][1]
            if y >= 25:
                events.append({'column': max(0, block['column']), 'y': y, 'kind': 'references',
                               'text': block_text(block).strip(), 'safe_top': y,
                               'spanning': block['column'] == -1})
    return sorted(events, key=lambda e: (e['column'], e['y']))


def _bibliography_starts(blocks, col, top, bottom):
    starts = []
    for block in blocks:
        if block['column'] != col:
            continue
        lines = block['lines']
        for index, line in enumerate(lines):
            y = line['bbox'][1]
            if top <= y < bottom and bibliography_block(block_text({'lines': lines[index:index + 8]})):
                starts.append(y)
    return starts


def exclude_captions(regions, number, blocks, columns):
    """A bibliography can continue past floats; their captions remain prose.

    Remove only the caption's band in the intersecting column. This leaves
    bibliography entries above/below it and in the other column protected.
    The font-scaled guard also leaves room for the translated glyph height.
    """
    captions = []
    for block in column_text_blocks(blocks, columns):
        lines = block['lines']
        heading_y = {event['y'] for event in _heading_events([block])}
        for index, line in enumerate(lines):
            if not CAPTION_START.match(block_text({'lines': [line]}).strip()):
                continue
            # A cited paper title beginning "Figure 2:" is still bibliography.
            # Inside a merged block a caption needs its own paragraph spacing.
            if index and not _paragraph_gap(lines, index):
                continue
            end = next((i for i in range(index + 1, len(lines))
                        if lines[i]['bbox'][1] in heading_y
                        or bibliography_block(block_text({'lines': lines[i:i + 8]}))), len(lines))
            caption = fitz.Rect(line['bbox'])
            for following in lines[index + 1:end]:
                caption |= fitz.Rect(following['bbox'])
            size = max((s['size'] for item in lines[index:end] for s in item['spans']), default=10)
            top, bottom = caption.y0 - .5 * size, caption.y1 + .5 * size
            if end < len(lines):
                bottom = min(bottom, lines[end]['bbox'][1])
            if index:
                top = max(top, lines[index - 1]['bbox'][3])
            captions.append((caption, top, bottom))
    for caption, top, bottom in captions:
        updated = []
        for region in regions:
            box = fitz.Rect(region['bbox'])
            if (region['page'] != number or caption.x1 <= box.x0 or caption.x0 >= box.x1
                    or bottom <= box.y0 or top >= box.y1):
                updated.append(region)
                continue
            if top > box.y0:
                updated.append({'page': number, 'bbox': [box.x0, box.y0, box.x1, top]})
            if bottom < box.y1:
                updated.append({'page': number, 'bbox': [box.x0, bottom, box.x1, box.y1]})
        regions[:] = updated


def _exclude_footer(regions, number, page, blocks):
    """Do not graft two clipped halves of a centred page number with references."""
    lines = [line for block in blocks for line in block.get('lines', [])]
    for line in lines:
        box = fitz.Rect(line['bbox'])
        if (box.y0 < .88 * page.rect.height or abs((box.x0 + box.x1) / 2 - page.rect.width / 2) > .08 * page.rect.width
                or not re.fullmatch(r'(?:page\s*)?\d{1,6}(?:\s*(?:of|/)\s*\d{1,6})?',
                                    block_text({'lines': [line]}).strip(), re.I)):
            continue
        if any(other is not line and other['bbox'][3] > box.y0 for other in lines):
            continue
        size = max((s['size'] for s in line['spans']), default=10)
        bottom = box.y0 - .5 * size
        for region in regions:
            if region['page'] == number:
                region['bbox'][3] = min(region['bbox'][3], bottom)
        regions[:] = [r for r in regions if r['bbox'][3] > r['bbox'][1]]


def detect_sections(path):
    reference_regions, layouts, headings, body_pages = [], [], [], []
    in_references = False
    with fitz.open(path) as document:
        for index, page in enumerate(document):
            blocks = [b for b in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
                      if b.get("type") == 0]
            columns = column_bounds(page, blocks)
            layouts.append({"page": index + 1, "columns": columns})
            logical_blocks = column_text_blocks(blocks, columns)
            events = _heading_events(logical_blocks)
            spanning = sorted((e for e in events if e['spanning']), key=lambda e: e['y'])
            top = 0
            # A full-width title starts a new horizontal band. Process columns
            # within each band so a centred appendix cannot hide the other
            # column's preceding references or protect its following prose.
            for boundary in [*spanning, None]:
                bottom = boundary['safe_top'] if boundary else page.rect.height
                band_events = [e for e in events if not e['spanning'] and top <= e['y'] < bottom]
                first_reference = next((e for e in band_events if e['kind'] == 'references'), None)
                for col, (x0, x1) in enumerate(columns):
                    cursor = top
                    if in_references and first_reference and col > first_reference['column']:
                        # Shared-footer bibliographies can have body text above
                        # them. Inspect native lines, including wrapped authors,
                        # instead of the first words of a merged physical block.
                        bib_y = _bibliography_starts(logical_blocks, col, top, bottom)
                        cursor = min(bib_y) if bib_y else first_reference['y']
                    for event in (e for e in band_events if e['column'] == col):
                        if in_references and event['safe_top'] > cursor:
                            reference_regions.append({'page': index + 1,
                                                      'bbox': [x0, cursor, x1, event['safe_top']]})
                        headings.append({'page': index + 1, **{k: event[k] for k in ('column', 'y', 'kind', 'text')}})
                        in_references = event['kind'] == 'references'
                        cursor = event['y']
                    if in_references and bottom > cursor:
                        reference_regions.append({'page': index + 1, 'bbox': [x0, cursor, x1, bottom]})
                if boundary:
                    headings.append({'page': index + 1,
                                     **{k: boundary[k] for k in ('column', 'y', 'kind', 'text')}, 'spanning': True})
                    in_references = boundary['kind'] == 'references'
                    top = boundary['y']
            exclude_captions(reference_regions, index + 1, blocks, columns)
            _exclude_footer(reference_regions, index + 1, page, blocks)
            if any(len(re.findall(r"[A-Za-z]{2,}", block_text({'lines': [line]}))) >= 3
                   and line['bbox'][1] >= 25
                   and not is_reference_region(index + 1, line['bbox'], reference_regions)
                   for b in logical_blocks for line in b['lines']):
                body_pages.append(index + 1)
    return {"reference_regions": reference_regions, "page_columns": layouts,
            "section_headings": headings, "body_pages": body_pages}


def is_reference_region(page, box, regions):
    rectangle = fitz.Rect(box)
    area = 0
    for region in regions:
        if region["page"] != page:
            continue
        area += (rectangle & fitz.Rect(region["bbox"])).get_area()
    return area > .6 * max(1, rectangle.get_area())


def prose_text_blocks(page, blocks, columns, regions):
    """Exclude reference lines without losing adjacent prose in the same block."""
    result = []
    for block in column_text_blocks(blocks, columns):
        pending = []

        def flush():
            if not pending:
                return
            box = fitz.Rect(pending[0]['bbox'])
            for line in pending[1:]:
                box |= fitz.Rect(line['bbox'])
            result.append({'lines': list(pending), 'bbox': list(box)})
            pending.clear()

        for line in block['lines']:
            if is_reference_region(page, line['bbox'], regions):
                flush()
            else:
                pending.append(line)
        flush()
    return result
