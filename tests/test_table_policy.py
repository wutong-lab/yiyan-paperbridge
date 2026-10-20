from copy import deepcopy

import pymupdf as fitz
import pytest

from paperbridge.document_context import DocumentContext, read_source_profile
from paperbridge.pdf_engine import analyze
from paperbridge.sections import detect_sections
from paperbridge.table_policy import TABLE_PRESERVED, apply_table_policy, is_table_content


def cell(**updates):
    return {"id": "p1-cell", "page": 1, "bbox": [55, 110, 145, 125],
            "source": "Training samples", "translation": "", "status": "pending",
            "reason": "", "notes": [], "kind": "table", **updates}


@pytest.mark.parametrize("label", ["table_caption", "table_footnote", "figure_caption"])
def test_table_rectangles_do_not_protect_captions_or_notes(label):
    assert not is_table_content(1, [55, 110, 145, 125], [{"page": 1, "bbox": [50, 100, 450, 300]}], layout_label=label)


def test_regions_are_page_scoped_and_do_not_capture_nearby_body():
    regions = [{"page": 1, "bbox": [50, 100, 450, 300]}]
    assert is_table_content(1, [55, 110, 145, 125], regions)
    assert not is_table_content(2, [55, 110, 145, 125], regions)
    assert not is_table_content(1, [55, 310, 145, 325], regions)
    assert not is_table_content(1, [0, 105, 560, 125], [{"page": 1, "bbox": [400, 100, 450, 300]}])


def test_disabled_tables_clear_cached_translations_but_keep_body():
    table = cell(translation="训练样本", status="translated", exported=True,
                 translation_audit=[{"message": "old issue"}])
    body = cell(id="body", kind="text", translation="正文", status="translated")
    before = deepcopy(body)
    apply_table_policy([table, body])
    assert table["status"] == "preserved" and table["reason"] == TABLE_PRESERVED
    assert not table["translation"] and not table["exported"] and not table.get("translation_audit")
    assert body == before
    apply_table_policy([table], True)
    assert table["status"] == "pending" and not table["translation"]


@pytest.mark.parametrize("reason", ["数值保留原文", "参考文献保留原文", "含数学表达式，保留原文", "文字区域重叠，保留原文待核对"])
def test_enabling_tables_never_removes_other_protections(reason):
    table = cell(status="preserved", reason=reason)
    before = deepcopy(table)
    apply_table_policy([table], False)
    apply_table_policy([table], True)
    assert table == before


def test_manual_table_edits_survive_disable_reopen_and_enable():
    table = cell(status="edited", translation="手动校订", exported=True)
    apply_table_policy([table])
    assert table["translation"] == ""
    reopened = deepcopy(table)
    apply_table_policy([reopened], True)
    assert reopened["status"] == "edited" and reopened["translation"] == "手动校订"


def test_formula_placeholder_edits_survive_fresh_layout_source():
    previous = cell(source="Result {v1}", status="edited", translation="结果 {v1}")
    fresh = cell(source="Result x", kind="paragraph", table_content=True)
    apply_table_policy([fresh], False, {previous["id"]: previous})
    apply_table_policy([fresh], True)
    assert fresh["status"] == "edited" and fresh["source"] == "Result {v1}"
    assert fresh["translation"] == "结果 {v1}"


def test_saved_edit_is_not_restored_to_changed_conservative_source():
    table = cell(status="edited", translation="原手动译文")
    apply_table_policy([table]); table["source"] = "Other observations"
    apply_table_policy([table], True)
    assert table["status"] == "pending" and not table["translation"]


def test_fallback_table_labels_are_not_joined_into_body_continuations():
    table = cell(kind="paragraph", table_content=True, layout_label="fallback_line",
                 source="The training observations are selected")
    assert not DocumentContext.is_body(table)


def test_native_table_metadata_excludes_cells_from_source_context(tmp_path):
    path = tmp_path / "table.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), "Scientific results remain translatable.", fontsize=12)
        page.insert_text((50, 95), "Table 1: Observation summary", fontsize=10)
        for x in [50, 230, 450]: page.draw_line((x, 110), (x, 200))
        for y in [110, 140, 170, 200]: page.draw_line((50, y), (450, y))
        for y, text in [(130, "Training samples"), (160, "Validation samples"), (190, "Further samples")]:
            page.insert_text((60, y), text, fontsize=10)
            page.insert_text((240, y), "42", fontsize=10)
        doc.save(path)
    analysis = analyze(path)
    regions = [{"page": 1, "bbox": box} for box in analysis["pages"][0]["table_regions"]]
    assert regions
    profile, _ = read_source_profile(path, detect_sections(path), excluded_table_regions=regions)
    text = " ".join(p["source"] for p in profile)
    assert "Scientific results" in text and "Table 1" in text
    assert "Training samples" not in text and "Validation samples" not in text
