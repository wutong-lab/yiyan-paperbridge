"""Shared opt-in policy for table text, independent of model or PDF engine."""
from copy import deepcopy

import pymupdf as fitz

TABLE_PRESERVED = "表格文字保留原文（未开启表格翻译）"
EDIT_FIELDS = ("source", "translation", "status", "reason", "notes", "exported",
               "translation_pipeline_version", "translation_code_sha256", "document_terms_signature")


def _saved_edit(segment, match_source):
    return {**deepcopy({k: segment[k] for k in EDIT_FIELDS if k in segment}),
            "_table_source": match_source}


def is_table_content(page, box, regions=(), *, layout_label="", kind=""):
    # Captions/notes identified by the layout model remain regular prose.
    if layout_label in {"table_caption", "table_footnote", "figure_caption"}:
        return False
    if kind == "table" or layout_label == "table":
        return True
    rectangle = fitz.Rect(box)
    if rectangle.is_empty or rectangle.is_infinite:
        return False
    center = (rectangle.tl + rectangle.br) / 2
    return any(item["page"] == page and fitz.Rect(item["bbox"]).contains(center)
               and (rectangle & fitz.Rect(item["bbox"])).get_area() >= .5 * rectangle.get_area()
               for item in regions)


def apply_table_policy(segments, translate_tables=False, previous=None):
    """Keep table text out of requests/exports while retaining manual edits.

    Only this setting's own preservation state is reversible. Formula, numeric,
    bibliography and overlap protections remain in force when enabled.
    """
    for segment in segments:
        if not (segment.get("table_content") or segment.get("kind") == "table"
                or segment.get("layout_label") == "table"):
            continue
        old = (previous or {}).get(segment.get("id"), {})
        # The full engine supplies content-derived paragraph IDs; raw source
        # and formula-placeholder source can differ while the ID stays stable.
        if old:
            if old.get("table_saved_edit"):
                segment["table_saved_edit"] = deepcopy(old["table_saved_edit"])
                segment["table_saved_edit"]["_table_source"] = segment.get("source")
            if not translate_tables and old.get("status") == "edited" and old.get("translation"):
                segment["table_saved_edit"] = _saved_edit(old, segment.get("source"))
        if translate_tables:
            if segment.get("reason") == TABLE_PRESERVED:
                saved = segment.get("table_saved_edit", {})
                if saved.get("_table_source", saved.get("source")) == segment.get("source") and saved.get("translation"):
                    segment.update(deepcopy({k: v for k, v in saved.items() if k in EDIT_FIELDS}))
                else:
                    segment.update(status="pending", reason="", notes=[], exported=False)
            continue
        if segment.get("status") == "edited" and segment.get("translation"):
            segment["table_saved_edit"] = _saved_edit(segment, segment.get("source"))
        if segment.get("status") != "preserved" or segment.get("reason") == TABLE_PRESERVED:
            segment.update(status="preserved", reason=TABLE_PRESERVED, translation="", notes=[], exported=False)
            segment.pop("translation_audit", None)
    return segments
