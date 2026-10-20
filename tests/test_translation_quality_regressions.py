"""Generic regressions observed while translating mathematics-heavy papers."""
import pytest

from paperbridge.full_worker import scientific_heading_reason
from paperbridge.translation_audit import english_residues, negation_missing
from paperbridge.translator import LocalTranslator, TranslationError, protect, restore


@pytest.mark.parametrize("source", [
    "4.1. DESI DR2 BAO", "4.2. DES-Dovekie SNe Ia", "2. CMB BAO", "3. JWST NIRCam",
])
def test_identifier_headings_do_not_require_invented_chinese(source):
    assert scientific_heading_reason(source, "title")
    assert not scientific_heading_reason(source, "plain text")


@pytest.mark.parametrize("source", [
    "4.3. RSD growth samples", "4. RESULTS", "DATA AND METHODS", "1. BAO RESULTS",
    "1. INTRODUCTION", "AGN evolution", "The CMB", "A new model",
    "THERE", "NEXT STEP", "1. LONG TERM",
])
def test_headings_with_real_prose_still_require_translation(source):
    assert not scientific_heading_reason(source, "title")


def test_unbracketed_math_functions_do_not_hide_real_english():
    source = "f is d ln D/d ln a; log(x) and sin theta are discussed."
    assert english_residues(source, "f 为 d ln D/d ln a，并讨论 log(x)。") == []
    assert english_residues(source, "其中 ln data 尚未处理。") == ["ln", "data"]
    assert english_residues("The log is stored.", "保存 log。") == ["log"]
    assert english_residues("We record data.", "采用 ln D。") == ["ln"]


@pytest.mark.parametrize("translation", ["这是描述性惩罚，而非贝叶斯证据。", "这些区间并非正式测量。"])
def test_alternative_negation_is_understood_by_generation_notes(monkeypatch, translation):
    local = LocalTranslator()
    monkeypatch.setattr(local, "load", lambda: None)
    monkeypatch.setattr(local, "_generate", lambda messages, budget: translation)
    result, notes = local.translate("These intervals are not formal measurements.", "", "通用科研", {}, polish=False)
    assert result == translation
    assert not negation_missing("These are not measurements.", result)
    assert "原文含否定或限制，请核对是否漏译" not in notes
    assert negation_missing("These are not measurements.", "这些测量非常精确。")


@pytest.mark.parametrize("source", [
    "The ratio is above unity.",
    "The response is below unity.",
    "The dark-energy fraction controls the departure from unity.",
    "The factor drives the response toward unity at high redshift.",
])
def test_unity_is_an_atomic_numeric_value_only_in_math_contexts(source):
    protected, tokens = protect(source)
    assert "unity" not in protected
    assert list(tokens.values()) == ["一"]
    assert restore("该量为<PB0>。", tokens) == "该量为一。"
    with pytest.raises(TranslationError, match="新数值"):
        restore("该量为<PB0>，另一个量是2。", tokens)


@pytest.mark.parametrize("source", [
    "They worked toward unity.", "The unity of physics is discussed.",
    "The model of unity is discussed.", "The text refers to the unity of the group.",
])
def test_nonmathematical_unity_remains_visible_for_translation(source):
    assert "unity" in protect(source)[0]


def test_math_unity_translation_succeeds_without_a_numeric_exception(monkeypatch):
    local = LocalTranslator()
    monkeypatch.setattr(local, "load", lambda: None)
    inputs = []

    def generate(messages, budget):
        inputs.append(messages)
        assert "<PB0>" in messages[-1]["content"]
        return "该比值高于<PB0>。"

    monkeypatch.setattr(local, "_generate", generate)
    result, notes = local.translate("The ratio is above unity.", "", "通用科研", {}, polish=False)
    assert result == "该比值高于一。"
    assert not notes
    assert len(inputs) == 1


@pytest.mark.parametrize("response", [
    {"SOURCE": "注：计数指独立观测。"},
    {"translation": ""},
    {"translation": "Note: Independent observations."},
])
def test_invalid_output_contract_never_becomes_an_accepted_translation(monkeypatch, response):
    local = LocalTranslator()
    monkeypatch.setattr(local, "_generate_json", lambda messages, budget, response_schema=None: response)
    with pytest.raises(TranslationError, match="完整中文译文"):
        local._generate([{"role": "user", "content": "A scientific note."}], 256)
