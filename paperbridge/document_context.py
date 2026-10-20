"""Source-grounded terminology and reading context, independent of PDF rendering.

Every paragraph keeps its original ID and box. Continuations are linked only
across a verified column/page boundary; model responses must still account for
each original block separately. No filename or paper-specific rules are used.
"""
import hashlib
import json
import re
from statistics import median

from .glossary import matched_terms, parse_custom
from .sections import block_text, prose_text_blocks

VERSION = "document-context-3"
BODY_LABELS = {"plain text", "text", "paragraph", "fallback_line", "body"}
STOP = set("a an the and or of in on for from with without to by as at is are was were be been being this that these those we our their its it they which where when while if than then also can may might could would should will have has had not no only very more most less such same both each all any into over under between using used use based shown show given following however therefore respectively et al see figure table equation section appendix results result method methods paper work study studies model models parameter parameters case cases value values example examples number numbers different further first second third one two three four five six seven eight nine ten".split())
STOP |= set("constrain constrains constrained calculate calculates calculated compute computes computed measure measures measured observe observes observed produce produces produced require requires required include includes included involve involves involved cause causes caused depend depends depended determine determines determined obtain obtains obtained allow allows allowed consider considers considered assume assumes assumed adopt adopts adopted find finds found infer infers inferred demonstrate demonstrates demonstrated indicate indicates indicated suggest suggests suggested predict predicts predicted yield yields yielded increase increases increased decrease decreases decreased".split())
NOUN_ENDINGS = ("tion", "sion", "ment", "ance", "ence", "ity", "ism", "ness", "ics", "metry", "graphy")
NOUNS = set("mass masses radius radii density densities lens lenses lensing image images halo halos haloes subhalo subhalos subhaloes flux ratio ratios function functions distribution distributions spectrum spectra field fields network networks source sources effect effects optical depth emission absorption star stars galaxy galaxies variable variables distance distances delay delays time delays light wave waves inference posterior prior likelihood kernel operator matrix matrices gradient gradients cell cells gene genes protein proteins bias sample samples population populations source plane power profile profiles approximation algorithm algorithms feature features loss curve curves state states energy temperature pressure current band gap signal noise response responses mapping boundary boundaries aperture apertures black hole holes particle particles uncertainty uncertainties fraction fractions equation equations sheet sheets shift shifts constraint constraints space process processes map maps".split())
NOUNS -= {"black", "optical"}  # adjectives cannot end a complete noun phrase


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def merge_terms(base, additions):
    result = dict(base)
    for source, target in additions.items():
        result = {k: v for k, v in result.items() if k.casefold() != source.casefold()}
        result[source] = target
    return result


def active_document_terms(job, base):
    saved = job.get("document_terminology", {})
    accepted, _ = consistent_term_proposals(saved.get("suggestions", saved.get("entries", [])), base)
    proposals = {e["source"]: e["target"] for e in accepted
                 if e.get("origin") == "model" and e.get("target")}
    # Existing curated/user vocabulary always wins over generated suggestions.
    return merge_terms(merge_terms(proposals, base), parse_custom(job.get("document_term_overrides", "")))


def consistent_term_proposals(proposals, base):
    """A generated longer phrase cannot override a curated subphrase."""
    accepted, conflicts = [], []
    for entry in proposals:
        if entry.get("origin") != "model" or not entry.get("target"):
            continue
        required = matched_terms(entry["source"], base)
        missing = {key: value for key, value in required.items() if value not in entry["target"]}
        if missing:
            conflicts.append({"source": entry["source"], "suggested": entry["target"], "required": missing})
        else:
            accepted.append(entry)
    return accepted, conflicts


def is_heading(segment):
    text = segment.get("source", "").strip()
    if not text or len(text) > 170 or re.search(r"[.!?;]$", text):
        return False
    if segment.get("layout_label") == "title":
        return True
    return bool(re.fullmatch(r"(?:\d{1,2}(?:\.\d{1,2})*\.?\s+|Appendix\s+[A-Z]:?\s+)[A-Z][A-Za-z ,/&()\-]{2,130}", text))


def reading_key(segment, sections):
    layout = next((p for p in sections.get("page_columns", []) if p["page"] == segment["page"]), {})
    columns = layout.get("columns", [[0, 10000]])
    x = (segment["bbox"][0] + segment["bbox"][2]) / 2
    column = next((i for i, (a, b) in enumerate(columns) if a <= x <= b), len(columns) - 1)
    # A title spanning both columns precedes the first column at the same y.
    if len(columns) > 1 and segment["bbox"][0] < columns[0][1] < segment["bbox"][2] and (is_heading(segment) or re.match(r"^Abstract\b", segment["source"], re.I)):
        column = 0
    return segment["page"], column, segment["bbox"][1], segment["bbox"][0]


def read_source_profile(path, sections):
    import pymupdf as fitz
    from .layout_policy import frontmatter_reason
    result, sizes = [], {}
    with fitz.open(path) as doc:
        for n, page in enumerate(doc, 1):
            sizes[n] = [page.rect.width, page.rect.height]
            blocks = [b for b in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"] if b.get("type") == 0]
            fonts = [s["size"] for b in blocks for line in b["lines"] for s in line["spans"] if len(re.findall(r"[A-Za-z]{2,}", s["text"])) >= 3]
            body_size = median(fonts) if fonts else 10
            columns = next((p['columns'] for p in sections.get('page_columns', []) if p['page'] == n),
                           [[0, page.rect.width]])
            blocks = prose_text_blocks(n, blocks, columns, sections.get('reference_regions', []))
            for i, block in enumerate(blocks):
                text = block_text(block).strip()
                box = list(block["bbox"])
                if box[1] < 25 or not text:
                    continue
                spans = [s for line in block["lines"] for s in line["spans"]]
                size = max((s["size"] for s in spans), default=body_size)
                bold = any("bold" in s.get("font", "").lower() or s.get("flags", 0) & 16 for s in spans)
                short = len(text) < 170 and len(re.findall(r"[A-Za-z]+", text)) < 22 and not re.search(r"[.!?;]$", text)
                heading = bool(short and (size > body_size * 1.2 or (bold and (text.isupper() or re.match(r"^(?:\d+(?:\.\d+)*\.?\s+|Appendix\b)", text, re.I)))))
                heading |= text.casefold() in {"abstract", "introduction", "methods", "results", "discussion", "conclusions", "acknowledgements", "data availability"}
                heading |= bool(n == 1 and short and text.isupper() and len(text.split()) >= 5 and box[1] < page.rect.height * .3 and box[2]-box[0] > page.rect.width * .5)
                if frontmatter_reason(text, n, box, page.rect.height) and not heading:
                    continue
                first_line = block_text({"lines": block["lines"][:1]}).strip()
                if len(block["lines"]) > 1 and (is_heading({"source": first_line}) or first_line.casefold() == "abstract") and not heading:
                    result.append({"id": f"source-{n}-{i}-heading", "page": n, "bbox": list(block["lines"][0]["bbox"]),
                                   "source": first_line, "font_size": size, "layout_label": "title", "status": "pending"})
                    text = block_text({"lines": block["lines"][1:]}).strip()
                    box[1] = block["lines"][1]["bbox"][1]
                label = "title" if heading else "figure_caption" if re.match(r"^(?:Figure|Fig\.|Table)\s*\d", text, re.I) else "plain text"
                result.append({"id": f"source-{n}-{i}", "page": n, "bbox": box, "source": text,
                               "font_size": size, "layout_label": label, "status": "pending"})
    return sorted(result, key=lambda s: reading_key(s, sections)), sizes


def term_candidates(segments, base, limit=16):
    """Only explicit acronym definitions or repeated multiword noun phrases."""
    corpus = [s for s in segments if s.get("status") != "preserved" and not s.get("footnote")]
    known = {k.casefold() for k in base}
    candidates = {}
    def add(source, seg, kind, acronym=None):
        source = re.sub(r"\s+", " ", source).strip(" ,;:.()")
        source = re.sub(r"^(?:(?:the|of|and|in|for|a|an)\s+)+", "", source, flags=re.I)
        if source.casefold() in known or not 5 <= len(source) <= 90:
            return
        key = source.casefold()
        if kind == "repeated" and any(re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", phrase) for phrase in known):
            # A shorter piece of a known technical phrase loses its qualifier;
            # leave it to sentence context rather than bind an ambiguous name.
            return
        entry = candidates.setdefault(key, {"source": source, "kind": kind, "pages": set(), "segments": set(), "examples": [], "acronym": acronym})
        entry["pages"].add(seg["page"]); entry["segments"].add(seg["id"])
        if len(entry["examples"]) < 2:
            pos = seg["source"].casefold().find(key)
            entry["examples"].append(seg["source"][max(0, pos - 100):pos + len(source) + 130])
        if kind == "definition":
            entry["kind"] = kind; entry["acronym"] = acronym
    for seg in corpus:
        text = seg["source"]
        for match in re.finditer(r"([A-Za-z][A-Za-z\-]+(?:\s+[A-Za-z][A-Za-z\-]+){1,7})\s*\(([A-Z][A-Z0-9]{1,7})\)", text):
            words, acronym = match[1].split(), match[2]
            # The regex may include a preceding clause; select an initial-matching suffix.
            for start in range(len(words)):
                suffix = words[start:]
                initials = "".join(w[0] for w in suffix if w.lower() not in {"of", "the", "and", "for", "in"}).upper()
                hyphen_initials = "".join(part[0] for w in suffix for part in w.split("-") if part and part.lower() not in {"of", "the", "and", "for", "in"}).upper()
                if len(suffix) >= 2 and acronym in {initials, hyphen_initials}:
                    add(" ".join(suffix), seg, "definition", acronym); break
        for chunk in re.split(r"[.!?;:,()\[\]{}<>\d]", text):
            words = re.findall(r"\b[A-Za-z][A-Za-z\-]*\b", chunk)
            for size in (4, 3, 2):
                for i in range(len(words) - size + 1):
                    phrase = words[i:i + size]
                    if any(w.lower() in STOP or len(w) < 3 or w.lower() in {"sion", "tion", "ment"} for w in phrase) or phrase[0][0].isupper():
                        continue
                    if phrase[-1].lower() not in NOUNS and not phrase[-1].lower().endswith(NOUN_ENDINGS):
                        continue
                    add(" ".join(phrase), seg, "repeated")
    entries = [e for e in candidates.values() if e["kind"] == "definition" or len(e["segments"]) >= 2]
    entries.sort(key=lambda e: (e["kind"] != "definition", -len(e["segments"]), -len(e["source"])))
    picked = []
    for e in entries:
        if any(e["source"].casefold() in p["source"].casefold() for p in picked):
            continue
        e["count"] = len(e.pop("segments")); e["pages"] = sorted(e["pages"])
        picked.append(e)
        if len(picked) >= limit:
            break
    return picked


def valid_term_proposals(candidates, proposals):
    allowed = {e["source"].casefold(): e for e in candidates}
    result, seen = [], set()
    if not isinstance(proposals, list):
        return result
    for p in proposals:
        if not isinstance(p, dict) or not isinstance(p.get("source"), str) or not isinstance(p.get("target"), str):
            continue
        key, target = p["source"].casefold(), p["target"].strip()
        if key not in allowed or key in seen or not 1 <= len(target) <= 45 or not re.search(r"[\u3400-\u9fff]", target):
            continue
        if re.search(r"[\d{}<>\n。；！？]|https?://|(?i:translation|译为|可能是)", target):
            continue
        # Permit scientific uppercase acronyms, but no copied English sentences.
        if any(not word.isupper() for word in re.findall(r"[A-Za-z]{2,}", target)):
            continue
        result.append({**allowed[key], "target": target, "origin": "model", "review": "suggested"})
        seen.add(key)
    return result


def glossary_entries(segments, base, proposals, overrides=""):
    entries = {}
    for seg in segments:
        if seg.get("status") == "preserved":
            continue
        for source, target in matched_terms(seg["source"], base).items():
            e = entries.setdefault(source.casefold(), {"source": source, "target": target, "origin": "library", "pages": set(), "count": 0, "review": "reference"})
            e["pages"].add(seg["page"]); e["count"] += 1
    for proposal in proposals:
        entries.setdefault(proposal["source"].casefold(), dict(proposal))
    for source, target in parse_custom(overrides).items():
        old = entries.get(source.casefold(), {})
        entries[source.casefold()] = {**old, "source": source, "target": target, "origin": "user", "review": "confirmed", "pages": old.get("pages", []), "count": old.get("count", 0)}
    return sorted([{**e, "pages": sorted(e["pages"])} for e in entries.values()], key=lambda e: (e["origin"] != "user", e["origin"] != "model", e["source"].casefold()))


class DocumentContext:
    def __init__(self, segments, sections, page_sizes, source_profile=(), table_regions=()):
        self.ordered = sorted(segments, key=lambda s: reading_key(s, sections))
        self.sections, self.sizes = sections, page_sizes
        self.headings = sorted([s for s in [*source_profile, *segments] if is_heading(s)], key=lambda s: reading_key(s, sections))
        titles = [s for s in self.headings if s["page"] == 1 and s["bbox"][2]-s["bbox"][0] > .4 * page_sizes.get(1, [600,800])[0] and not re.match(r"\d+\.?\s|arXiv\b|https?://|Accepted\b|Submitted\b|Abstract\b|Introduction\b", s["source"], re.I)]
        self.title = titles[0]["source"][:700] if titles else ""
        self.abstract = ""
        in_abstract = False
        for s in source_profile:
            if s["page"] == 1 and re.match(r"^Abstract\b", s["source"], re.I) and len(s["source"]) > 20:
                self.abstract = re.sub(r"^Abstract\s*[:.—-]?\s*", "", s["source"], flags=re.I)
                break
            if is_heading(s):
                if in_abstract: break
                in_abstract = s["source"].strip().casefold() == "abstract"
            elif in_abstract:
                self.abstract += " " + s["source"]
        self.abstract = self.abstract.strip()[:550]
        self.contexts, self.links = {}, {}
        self.table_count = 0
        body = [s for s in self.ordered if self.is_body(s)]
        for i, s in enumerate(body):
            previous = body[i-1]["source"][-600:] if i and self.section_for(body[i-1]) == self.section_for(s) else ""
            following = body[i+1]["source"][:600] if i+1 < len(body) and self.section_for(body[i+1]) == self.section_for(s) else ""
            if i == 0:
                neighbors = [p for p in source_profile if self.is_body(p) and p["page"] < s["page"] and self.section_for(p) == self.section_for(s)]
                previous = neighbors[-1]["source"][-600:] if neighbors else ""
            if i == len(body)-1:
                neighbors = [p for p in source_profile if self.is_body(p) and p["page"] > s["page"] and self.section_for(p) == self.section_for(s)]
                following = neighbors[0]["source"][:600] if neighbors else ""
            self.contexts[s["id"]] = {"论文标题": self.title, "摘要背景": self.abstract, "当前章节": self.section_for(s),
                "上一段原文": previous, "下一段原文": following}
        self.add_table_context(table_regions)
        claimed = set()
        for a, b in zip(body, body[1:]):
            if a["id"] in claimed or b["id"] in claimed or not self.continues(a, b):
                continue
            group = {"id": signature([a["id"], b["id"]])[:16], "members": [a["id"], b["id"]],
                     "pages": [a["page"], b["page"]], "kind": "page" if a["page"] != b["page"] else "column" if reading_key(a, sections)[1] != reading_key(b, sections)[1] else "fragment"}
            for s in (a, b):
                self.links[s["id"]] = group
                self.contexts[s["id"]]["续接原文"] = [{"page": p["page"], "text": p["source"]} for p in (a, b)]
                claimed.add(s["id"])

    def add_table_context(self, regions):
        """Use detected table rectangles and physical label rows, not page IDs.

        Numeric cells are excluded from the prompt. Each label keeps its own
        PDF box; nearby tables and plot labels cannot leak into this context.
        """
        for region in regions:
            page, box = region["page"], region["bbox"]
            cells = [s for s in self.ordered if s["page"] == page
                     and box[0] <= (s["bbox"][0]+s["bbox"][2])/2 <= box[2]
                     and box[1] <= (s["bbox"][1]+s["bbox"][3])/2 <= box[3]]
            labels = [s for s in cells if s.get("status") != "preserved"
                      and len(s["source"]) <= 100 and len(s["source"].split()) <= 8
                      and re.search(r"[A-Za-z]{2,}", s["source"])]
            if not labels:
                continue
            captions = [s for s in self.ordered if s["page"] == page
                        and s.get("layout_label") == "table_caption"
                        and max(box[0], s["bbox"][0]) < min(box[2], s["bbox"][2])]
            distance = lambda s: max(0, box[1]-s["bbox"][3], s["bbox"][1]-box[3])
            caption = min(captions, key=distance) if captions else None
            caption_text = caption["source"][:600] if caption and distance(caption) <= 90 else ""
            ordered = sorted(labels, key=lambda s: (s["bbox"][1], s["bbox"][0]))
            table_labels = list(dict.fromkeys(s["source"] for s in ordered))[:28]
            self.table_count += 1
            for s in labels:
                b = s["bbox"]; height = max(1, b[3]-b[1]); x = (b[0]+b[2])/2
                above = [p for p in labels if p is not s and p["bbox"][3] <= b[1]+height*.2
                         and 0 < b[1]-p["bbox"][1] < max(24, height*5)]
                parent = ""
                if above:
                    row_y = max((p["bbox"][1]+p["bbox"][3])/2 for p in above)
                    row = [p for p in above if abs((p["bbox"][1]+p["bbox"][3])/2-row_y) <= height*.6]
                    if len(row) >= 2:
                        parent = min(row, key=lambda p: abs((p["bbox"][0]+p["bbox"][2])/2-x))["source"]
                neighbors = sorted([p for p in labels if p is not s
                                    and abs((p["bbox"][1]+p["bbox"][3])/2-(b[1]+b[3])/2) <= height],
                                   key=lambda p: abs((p["bbox"][0]+p["bbox"][2])/2-x))[:4]
                ctx = self.contexts.setdefault(s["id"], {"论文标题": self.title, "摘要背景": self.abstract,
                                                        "当前章节": self.section_for(s)})
                ctx["表格语境"] = {"表题": caption_text, "上级表头": parent,
                                   "相邻标签": [p["source"] for p in neighbors], "表内标签": table_labels}

    @staticmethod
    def is_body(s):
        return s.get("status") != "preserved" and not s.get("footnote") and s.get("layout_label") in BODY_LABELS and not is_heading(s) and len(re.findall(r"[A-Za-z]{2,}", s["source"])) >= 4

    def section_for(self, s):
        before = [h for h in self.headings if reading_key(h, self.sections) <= reading_key(s, self.sections) and h["source"] != self.title]
        return before[-1]["source"][:170] if before else ""

    def continues(self, a, b):
        ka, kb = reading_key(a, self.sections), reading_key(b, self.sections)
        same_page_column = a["page"] == b["page"] and kb[1] == ka[1] + 1
        fragment = (a["page"] == b["page"] and ka[1] == kb[1]
                    and 0 <= b["bbox"][1]-a["bbox"][3] <= .6 * max(1, a.get("font_size", 10)))
        next_page = b["page"] == a["page"] + 1 and kb[1] == 0
        if not (same_page_column or next_page or fragment) or len(a["source"]) + len(b["source"]) > 4200:
            return False
        if next_page:
            columns = next((p["columns"] for p in self.sections.get("page_columns", []) if p["page"] == a["page"]), [[0, 10000]])
            if ka[1] != len(columns)-1: return False
        if any(ka < reading_key(h, self.sections) < kb for h in self.headings):
            return False
        if any(ka < reading_key(s, self.sections) < kb and s.get("layout_label") in {"figure_caption", "table_caption"} for s in self.ordered):
            return False
        if any(ka < reading_key(s, self.sections) < kb and s.get("reason") == "独立公式保留原文"
               and .08 * self.sizes.get(s["page"], [0,800])[1] < s["bbox"][1] < .93 * self.sizes.get(s["page"], [0,800])[1]
               for s in self.ordered):
            return False
        tail = re.sub(r"</?b\d+>", "", a["source"]).rstrip()
        head = re.sub(r"^(?:\{v\d+\}|</?b\d+>|\s)+", "", b["source"])
        if not tail or re.search(r"[.!?;:]\s*[\])'\"]?$", tail) or not re.match(r"[a-z]", head):
            return False
        ha = self.sizes.get(a["page"], [0, 800])[1]; hb = self.sizes.get(b["page"], [0, 800])[1]
        if not fragment and (a["bbox"][3] < .60 * ha or b["bbox"][1] > .42 * hb):
            return False
        if abs(a.get("font_size", 10)-b.get("font_size", 10)) > 1.5:
            return False
        return True

    def for_segment(self, s):
        return self.contexts.get(s["id"], {"论文标题": self.title, "当前章节": self.section_for(s)})

    def summary(self):
        groups = {g["id"]: g for g in self.links.values()}
        return {"version": VERSION, "section_count": len({h["source"] for h in self.headings}),
                "tables_with_context": self.table_count,
                "continuation_groups": list(groups.values()), "joint_success": [], "joint_fallback": [],
                "context_chars_max": max((len(json.dumps(c, ensure_ascii=False)) for c in self.contexts.values()), default=0)}
