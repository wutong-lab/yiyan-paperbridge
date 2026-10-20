import json
import re

from paperbridge.document_context import DocumentContext, active_document_terms, consistent_term_proposals
from paperbridge.glossary import contextual_glossary, normalize_line_hyphens, terms_for
from paperbridge.terminology import atomic_terms, enforced_terms, pack_terms
from paperbridge.translator import LocalTranslator
from paperbridge.full_worker import translation_cache_key


def block(sid, source, box, label="fallback_line"):
    return {"id": sid, "page": 1, "bbox": box, "source": source,
            "layout_label": label, "status": "pending", "font_size": 8}


def table_plan():
    blocks = [block("caption-a", "Table 1: Image robustness under different noise types.", [20, 45, 220, 65], "table_caption"),
              block("noise", "Noise", [30, 80, 70, 90]),
              block("blur", "Blur", [150, 80, 180, 90]),
              block("shot-a", "Shot", [35, 100, 65, 110]),
              block("gaussian", "Gaussian", [80, 100, 120, 110]),
              block("caption-b", "Table 2: Camera settings.", [300, 45, 500, 65], "table_caption"),
              block("camera", "Camera", [320, 80, 370, 90]),
              block("metric", "Metric", [440, 80, 480, 90]),
              block("shot-b", "Shot", [330, 100, 360, 110]),
              block("outside", "Shot", [20, 400, 60, 410])]
    regions = [{"page": 1, "bbox": [20, 75, 220, 170]},
               {"page": 1, "bbox": [300, 75, 510, 170]}]
    return DocumentContext(blocks, {}, {1: [600, 800]}, table_regions=regions), blocks


def test_table_context_uses_physical_parent_and_separates_neighboring_tables():
    plan, blocks = table_plan()
    a, b = plan.for_segment(blocks[3]), plan.for_segment(blocks[8])
    assert a["表格语境"]["上级表头"] == "Noise"
    assert b["表格语境"]["上级表头"] == "Camera"
    assert "noise types" in a["表格语境"]["表题"]
    assert "Camera" not in a["表格语境"]["表内标签"]
    assert "表格语境" not in plan.for_segment(blocks[-1])
    assert translation_cache_key("p", "Shot", {}, a) != translation_cache_key("p", "Shot", {}, b)
    assert plan.summary()["tables_with_context"] == 2


def test_short_homonym_only_uses_its_own_group_and_user_choice_wins():
    plan, blocks = table_plan()
    terms = terms_for("engineering")
    assert "Shot" not in pack_terms("engineering")
    assert "corruption" not in terms  # a domain selection alone is insufficient
    noise = contextual_glossary("Shot", terms, "engineering", reference=plan.for_segment(blocks[3]))
    camera = contextual_glossary("Shot", terms, "engineering", reference=plan.for_segment(blocks[8]))
    assert noise["Shot"] == "散粒"
    assert "Shot" not in camera
    assert contextual_glossary("Shot", {**terms, "SHOT": "用户表头"}, "engineering",
                              reference=plan.for_segment(blocks[3]))["SHOT"] == "用户表头"
    assert "Shot" not in contextual_glossary("Shot", terms_for("biomed"), "biomed",
                                              reference=plan.for_segment(blocks[3]))


def test_generated_longer_phrase_cannot_shadow_curated_shorter_term():
    base = {"corruption robustness": "退化鲁棒性"}
    proposals = [{"source": "comprehensive corruption robustness", "target": "综合损坏鲁棒性", "origin": "model"},
                 {"source": "adaptive corruption robustness", "target": "自适应退化鲁棒性", "origin": "model"}]
    accepted, conflicts = consistent_term_proposals(proposals, base)
    assert len(accepted) == len(conflicts) == 1
    assert conflicts[0]["required"] == base
    job = {"document_terminology": {"suggestions": proposals}}
    active = active_document_terms(job, base)
    assert "comprehensive corruption robustness" not in active
    assert active["adaptive corruption robustness"] == "自适应退化鲁棒性"
    job["document_term_overrides"] = "comprehensive corruption robustness = 本篇用户译名"
    assert active_document_terms(job, base)["comprehensive corruption robustness"] == "本篇用户译名"
    assert proposals[0]["target"] == "综合损坏鲁棒性"  # original evidence remains unchanged


def test_image_context_keeps_corruption_out_of_unrelated_fields():
    reference = {"论文标题": "Robust classification of images", "摘要背景": "We compare natural image corruptions."}
    terms = contextual_glossary("", terms_for("engineering"), "engineering", reference=reference)
    assert terms["corruption"] == "退化"
    assert "corruption" not in contextual_glossary("We study political corruption.", terms_for("engineering"), "engineering")
    assert "corruption" not in contextual_glossary("We examine image corruption.", terms_for("materials"), "materials", reference=reference)
    assert contextual_glossary("", {**terms_for("engineering"), "corruption": "用户退化名"},
                              "engineering", reference=reference)["corruption"] == "用户退化名"


def test_long_paragraph_uses_fixed_nouns_without_relaxing_numeric_protection(monkeypatch):
    local = LocalTranslator()
    monkeypatch.setattr(local, "load", lambda: None)
    reference = {"论文标题": "Robust image recognition", "摘要背景": "Common image corruptions."}
    source = "Image corruption affects the neural network in adverse conditions. " * 5 + "We evaluate 16 types at 95% confidence."
    captured = []
    def generate(messages, budget):
        content = messages[-1]["content"]
        value = json.JSONDecoder().raw_decode(content[content.index("{"):])[0]["SOURCE"]
        captured.append(value)
        return "图像实验结果为" + "，".join(re.findall(r"<PB\d+>", value)) + "。"
    monkeypatch.setattr(local, "_generate", generate)
    translated, _ = local.translate(source, reference, "工程与计算机", terms_for("engineering"), polish=False)
    assert "Image corruption" not in captured[0]
    assert translated.count("图像退化") == 5
    assert "16" in translated and "95%" in translated
    assert enforced_terms("shot noise", terms_for("engineering")) == {"shot noise": "散粒噪声"}
    assert "shot noise" in atomic_terms("We measure shot noise.", terms_for("engineering"))


def test_dimensional_pdf_word_join_is_repaired_without_touching_ids_or_code():
    source = "We designed benchmarks for3Dobject detection and 2Dimage recognition."
    assert normalize_line_hyphens(source, {}) == "We designed benchmarks for 3D object detection and 2D image recognition."
    untouched = "We use for3DObject, TOI11, GW170817 and the X3Dobject identifier."
    assert normalize_line_hyphens(untouched, {}) == untouched


def test_enforced_suffix_never_replaces_the_complete_prescribed_phrase():
    reference = {"论文标题": "Robust image classification"}
    terms = contextual_glossary("", terms_for("engineering"), "engineering", reference=reference)
    result = enforced_terms("We compare common corruptions at different corruption severities.", terms)
    assert result == {"corruption severities": "退化严重程度", "common corruptions": "常见图像退化"}
    assert "corruptions" not in result and "corruption" not in result
    assert "corruption" not in terms_for("astronomy")
