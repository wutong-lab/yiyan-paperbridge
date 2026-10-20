"""Identify scientific identifiers that should remain readable in English."""
import re
from collections import Counter


def merge_edge_fragments(paragraphs, edited_sources=()):
    """Join a clipped word suffix on the last line without widening a column.

    A source PDF can itself print a long word into the gutter. Tiny detector
    rectangles then contain only its last letters. Require physical adjacency,
    a shared last baseline and lowercase suffixes; never join full sentences or
    change an already edited paragraph's source.
    """
    removed, edges = set(), {}
    changed = True
    while changed:
        changed = False
        for fragment in paragraphs:
            if id(fragment) in removed:
                continue
            value = fragment.unicode or ""
            numeric = bool(re.fullmatch(r"\d+(?:\.\d+)*\.", value))
            if fragment.layout_label not in {"fallback_line", "plain text"} or not (numeric or re.fullmatch(r"[a-z]{1,12}-?", value)):
                continue
            for parent in paragraphs:
                if parent is fragment or id(parent) in removed or (parent.unicode or "") in edited_sources:
                    continue
                title = parent.layout_label == "title"
                if not title and (parent.layout_label not in {"plain text", "figure_caption", "table_caption"} or len(parent.unicode or "") < 20):
                    continue
                if not re.search(r"[A-Za-z]$", parent.unicode or ""):
                    continue
                a, b = parent.box, fragment.box
                size = parent.pdf_style.font_size if parent.pdf_style else 10
                overlap = max(0, min(a.y2, b.y2) - max(a.y, b.y)) / max(1, b.y2 - b.y)
                edge = edges.get(id(parent), a.x2)
                suffix = not numeric and 0 <= b.x - edge < (.2 if title else .1) * size
                prefix = title and numeric and 0 <= a.x - b.x2 < .2 * size
                if overlap <= .7 or not (prefix or suffix) or (not title and abs(b.y - a.y) > .35 * size):
                    continue
                if prefix:
                    parent.unicode = value + parent.unicode
                    parent.pdf_paragraph_composition = fragment.pdf_paragraph_composition + parent.pdf_paragraph_composition
                    a.x = b.x
                else:
                    parent.unicode += value
                    parent.pdf_paragraph_composition.extend(fragment.pdf_paragraph_composition)
                    edges[id(parent)] = b.x2
                    if title:
                        a.x2 = b.x2
                if title:
                    a.y, a.y2 = min(a.y, b.y), max(a.y2, b.y2)
                removed.add(id(fragment))
                changed = True
                break
    return [paragraph for paragraph in paragraphs if id(paragraph) not in removed]


def identifier_suffix(source, box, neighbors):
    """Recognize a font-split suffix beside a catalogue ID, without guessing it."""
    if not re.fullmatch(r'[a-z]{1,8}', source.strip()):
        return False
    height = max(1, box[3] - box[1])
    for text, other in neighbors:
        overlap = min(box[3], other[3]) - max(box[1], other[1])
        if (0 <= box[0] - other[2] <= 2 * height and overlap > .5 * height
                and re.search(r'\b[A-Z]{2,8}e?\s*[12]\d{3}\s*$', text)):
            return True
    return False


def split_unicode_lines(compositions):
    """Keep explicit editor line breaks without changing formula components."""
    from .il_snapshot import snapshot_graph
    groups = [[]]
    for composition in compositions:
        text = composition.pdf_same_style_unicode_characters
        if text is None:
            return None  # Mixed formula/layout content needs its usual path.
        for index, part in enumerate((text.unicode or '').split('\n')):
            if index:
                groups.append([])
            if part:
                copy = snapshot_graph(composition)
                copy.pdf_same_style_unicode_characters.unicode = part
                groups[-1].append(copy)
    return groups if len(groups) > 1 and all(groups) else None


def literal_reason(source, layout_label):
    text = source.strip()
    if re.fullmatch(r"(?:\d+(?:\.\d+)*\.?\s+)?(?:SNe?\s*)?[12]\d{3}[a-z]{1,10}", text):
        return "科学对象编号保留原文"
    if re.fullmatch(r"[xX]{3,}", text):
        return "原稿占位内容保留原文"
    author = r"[A-Za-z][A-Za-z’'-]+(?:\s*(?:&|and)\s*[A-Za-z][A-Za-z’'-]+|\s+et\s*al\.)?"
    year = r"(?:19|20)\d{2}[a-z]?"
    citation = rf"(?:\({author}\s+{year}\)|{author}\s*\(\s*{year}\s*\)|{author}\s+{year})"
    if re.fullmatch(rf"(?:[A-Z][A-Z0-9/+–-]*\s+)?{citation}", text):
        return "作者引文与学术名称保留原文"
    no_formulas = re.sub(r"\{v\d+\}", "", text)
    if re.fullmatch(rf"{citation}(?:\s*[;,]\s*{citation})+", no_formulas):
        return "作者引文与学术名称保留原文"
    unit = r"(?:[fpnμµumkMG]?(?:Hz|m|s|g|K|Jy)|erg|keV|MeV|GeV|eV|kpc|Mpc|Gpc|pc|yr|mag|dex|deg|arcsec|arcmin|mas)"
    punctuation = r"[\s().,^ˆ0-9+−*/×–-]*"
    if re.fullmatch(punctuation + "(?:" + unit + punctuation + ")+", no_formulas):
        return "公式函数或单位保留原文"
    words = set(re.findall(r"[A-Za-z]{2,}", no_formulas))
    if (words and all(re.fullmatch(unit, word) or re.fullmatch(r'[A-Z]{1,2}', word) for word in words)
            and re.search(r"\{v\d+\}|[α-ωΑ-Ω=()]", text)):
        return "公式函数或单位保留原文"
    if layout_label == "fallback_line":
        # These are plot/table identifiers, not English sentences. Do not map
        # e.g. EPL to an unrelated journal or force a detector name into Chinese.
        without_formulas = re.sub(r"\{v\d+\}", "", text).strip()
        # Table rows often consist of CamelCase model names, dataset IDs and
        # unchanged reference markers. Each token must independently look like
        # an identifier; mixed natural-language headings still get translated.
        tokens = without_formulas.split()
        identifier = r"[A-Z][A-Za-z0-9]*(?:[_./@+–-][A-Za-z0-9]+)*"
        signature = r"[A-Z]{2}|[a-z][A-Z]|\d|[_/@+–-]"
        if tokens and all(re.fullmatch(identifier, token) and re.search(signature, token) for token in tokens):
            return "图表中的模型名或数据集编号保留原文"
        if re.fullmatch(r"[a-z]{1,3}", without_formulas):
            return "图表中的短拉丁字母标签保留原文"
        if re.search(r"\d", without_formulas) and re.fullmatch(r"[A-Z][A-Z0-9.+/−–-]*(?:\s+[A-Z0-9][A-Z0-9.+/−–-]*)*", without_formulas):
            return "科学对象编号保留原文"
        if re.fullmatch(r"[A-Z][A-Z0-9]*(?:[/+: ,–-]+[A-Z][A-Z0-9]*)*", without_formulas):
            return "科学缩写或名称保留原文"
        if re.fullmatch(r"(?:aLIGO|LIGO(?:\s+A\+?|\s+Voyager)?|CosmoDC2|EMBS|ET|CE|sBBH)", without_formulas):
            return "探测器或科学模型名称保留原文"
        if re.match(r"^(?:ln|log)\s*\(", text):
            return "公式函数或单位保留原文"
        words = set(re.findall(r"[A-Za-z]{2,}", without_formulas))
        if words and words <= {"Gpc", "Mpc", "Hz", "keV", "yr"}:
            return "公式函数或单位保留原文"
        if re.fullmatch(r"[\s().,^ˆ0-9+-]*(?:(?:ln|log|Hz|keV|eV|Gpc|Mpc|yr|R)[\s().,^ˆ0-9+-]*)+", without_formulas):
            return "公式函数或单位保留原文"
    return ""


def dominant_body_font(paragraphs):
    weights = Counter()
    for paragraph in paragraphs:
        words = len(re.findall(r"[A-Za-z]{2,}", paragraph.unicode or ""))
        if paragraph.layout_label == "plain text" and paragraph.pdf_style and words >= 20:
            weights[round(paragraph.pdf_style.font_size, 2)] += words
    return weights.most_common(1)[0][0] if weights else None


def is_natural_footnote(source, box, label, page_height=792, *, font_size=None, body_font_size=None):
    without_links = re.sub(r"https?://\S+", "", source)
    small = font_size is not None and body_font_size is not None and font_size < .88 * body_font_size
    note_layout = label == "abandon" or (label == "plain text" and small)
    numbered = bool(re.match(r"^(?:\d+|[*†‡])\s*(?=[A-Za-z])", source))
    words = len(re.findall(r"[A-Za-z]{2,}", without_links))
    # A note can continue into the other column without repeating its label.
    # Require measured small type and prose, rather than promoting every footer.
    continuation = small and words >= 12 and bool(re.search(r"\b(?:is|are|was|were|could|would|can|may|should|must)\b", without_links, re.I))
    continuation = continuation and not re.search(r"\bcopyright\b|all rights reserved|\bISSN\b|^\s*(?:submitted|received|accepted|published)\b", source, re.I)
    return note_layout and box[1] > .69 * page_height and words >= 8 and (numbered or continuation)


def frontmatter_reason(source, page, box, page_height, *, layout_label=None):
    """Preserve author lists without forcing a model to invent Chinese names."""
    if page != 1 or layout_label == "title":
        return ""
    text = re.sub(r"\{v\d+\}|</?b\d+>|[\d¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", source)
    # Author names may touch 'and' at a PDF font boundary. Normalize only
    # this classifier's view; never alter the preserved PDF/name spelling.
    text = re.sub(r"\band(?=[A-Z])", "and ", text)
    if re.search(r"\b(?:is|are|was|were|has|have|we|using|present|show|find|examine|suggest|predict|observed|provide|investigate|demonstrate|derive|measure)\b", text, re.I):
        return ""
    institutions = re.findall(r"\b(?:University|Institute|Center|Centre|Department|Laboratory|School|Observatory|Academy|College)\b", text, re.I)
    if len(source) < 5000 and box[1] < .96 * page_height and len(institutions) >= 2 and re.search(r"\b\d{5,6}\b", source):
        return "作者机构与通讯地址保留原文"
    if box[1] > .5 * page_height or len(source) > 1000:
        return ""
    if institutions:
        if re.search(r"[A-Za-z0-9._%+-]+@|https?://", source):
            return "作者机构与通讯地址保留原文"
        return ""
    person = r"(?:(?:[A-Z]\.\s*){1,3}[A-Z][A-Za-z’'-]+|[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+)"
    names = re.findall(r"(?:^|[,·;]|\band\b)\s*" + person, text)
    if len(names) >= 2:
        return "作者姓名保留原文"
    return ""
