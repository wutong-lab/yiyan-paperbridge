import pytest

from paperbridge.glossary import normalize_line_hyphens, terms_for
from paperbridge.layout_policy import literal_reason, frontmatter_reason


@pytest.mark.parametrize("text", [
    "CVM-Net {v1}", "OriCNN {v1}", "SAFA {v1}", "L2LTR {v1}", "TransGeo {v1}",
    "Dataset_val-C", "Dataset_val-C Dataset_test-C-ALL", "ABC_val-C XYZ-C-ALL", "cor",
])
def test_table_identifiers_are_preserved_without_requesting_chinese(text):
    assert literal_reason(text, "fallback_line")


@pytest.mark.parametrize("text", ["Mean accuracy", "Average recall", "Results on datasets", "Methods and models"])
def test_regular_table_prose_still_translates(text):
    assert not literal_reason(text, "fallback_line")


def test_font_boundaries_restore_prose_but_keep_identifiers_and_urls():
    text = "Results of8 cross-view methods with5 levels andL2LTR on Dataset2 at https://example.org/of8."
    normalized = normalize_line_hyphens(text, {})
    assert "of 8" in normalized and "with 5" in normalized and "and L2LTR" in normalized
    assert "Dataset2" in normalized and "https://example.org/of8" in normalized
    assert normalize_line_hyphens("TOI11 and SN2025abc were observed.", {}) == "TOI11 and SN2025abc were observed."
    assert normalize_line_hyphens("The values of the object were unchanged.", {}) == "The values of the object were unchanged."
    code = "We use forEach, withDataFrame and andThen in the implementation."
    assert normalize_line_hyphens(code, {}) == code


def test_frontmatter_institution_and_email_are_not_prose():
    text = "College of Engineering, Example University, City, Country author@example.edu https://example.edu"
    assert frontmatter_reason(text, 1, [60, 120, 540, 175], 792)
    assert not frontmatter_reason("We use Example University observations to examine the model.", 1, [60, 120, 540, 175], 792)


def test_author_names_with_font_join_stay_literal_but_titles_still_translate():
    assert frontmatter_reason("Aiden Brooks andMira Chen{v1}", 1, [60, 120, 540, 150], 792,
                             layout_label="plain text") == "作者姓名保留原文"
    assert not frontmatter_reason("Scientific Observations andComputational Analysis", 1,
                                  [60, 60, 540, 110], 792, layout_label="title")


def test_computer_vision_terms_are_available_beyond_one_document():
    terms = terms_for("engineering", "")
    assert terms["image corruption"] == "图像退化"
    assert terms["data augmentation"] == "数据增强"
    assert terms["cross-view geo-localization"] == "跨视角地理定位"
    assert terms_for("engineering", "image corruption = 图像损坏")["image corruption"] == "图像损坏"


def test_math_check_uses_visible_glyphs_when_pdf_contains_actualtext(tmp_path):
    import pymupdf as fitz
    from paperbridge.quality import inspect_pdf
    original = tmp_path / "annotated.pdf"
    rendered = tmp_path / "painted.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((70, 100), "ab", fontname="symb", fontsize=15)
        document.save(rendered)
        xref = page.get_contents()[0]
        content = document.xref_stream(xref)
        actual = (b"\xfe\xff" + r"\sum_{i=1}^{n} x_i".encode("utf-16-be")).hex().encode()
        document.update_stream(xref, b"/Span << /ActualText <" + actual + b"> >> BDC\n" + content + b"\nEMC")
        document.save(original)
    result = inspect_pdf(original, rendered, [])
    assert result["structural_checks_passed"]
    assert result["pages"][0]["math_symbol_inventory_preserved"]
    with fitz.open() as document:
        document.new_page().insert_text((70, 100), "ac", fontname="symb", fontsize=15)
        changed = tmp_path / "changed.pdf"
        document.save(changed)
    assert not inspect_pdf(original, changed, [])["structural_checks_passed"]
