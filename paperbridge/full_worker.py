"""Isolated scientific PDF translation with direct, offline MLX inference.

BabelDOC 0.6.4 is deliberately pinned: the tracking hooks below target its
internal paragraph API. No BabelDOC cloud translator is instantiated.
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from .glossary import DOMAINS, terms_for, contextual_glossary
from .layout_runtime import initialize, ready
from .layout_policy import literal_reason, identifier_suffix, split_unicode_lines, is_natural_footnote, frontmatter_reason, merge_edge_fragments, dominant_body_font
from .il_snapshot import snapshot_graph
from .formula_policy import numeric_script_parser, readable_numeric_hint, has_numeric_scripts
from .sections import detect_sections, is_reference_region
from .translation_audit import audit_translation
from .review import has_issues, completion_message
from .translator import ROOT, LocalTranslator, TranslationError
from .paths import CODE_ROOT, WINDOWS
from .local_runtime import LocalRuntimeError
from .document_context import (DocumentContext, VERSION as CONTEXT_VERSION, read_source_profile,
                               term_candidates, glossary_entries, active_document_terms, signature, consistent_term_proposals)

PIPELINE_VERSION = "full-9"


def write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def emit(event, **data):
    print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


def block_network():
    """A translation worker cannot connect to the internet, even by accident."""
    def denied(*args, **kwargs):
        raise OSError("论文翻译阶段禁止网络连接；请先下载本地资源")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def validate_edit(source, translation):
    pattern = r"\{v\d+\}|</?b\d+>|<PB\d+>"
    if Counter(re.findall(pattern, source)) != Counter(re.findall(pattern, translation)):
        raise ValueError("公式占位符必须完整保留，例如 {v1}；请只修改中文文字")
    clean = lambda value: re.sub(pattern, "", value)
    numbers = lambda value: Counter(re.findall(r"\d+(?:[.,]\d+)*", clean(value)))
    if numbers(source) != numbers(translation):
        raise ValueError("修改不能增删原文数值或引文年份，请核对后再保存")
    if not re.search(r"[\u3400-\u9fff]", translation):
        raise ValueError("请填写中文译文")


def count_words(text):
    return len(re.findall(r"[A-Za-z]{2,}", re.sub(r"\{v\d+\}|</?b\d+>", "", text)))


def translation_cache_key(pipeline_digest, source, formula_hints, title_context):
    # Identical placeholder text can represent different physical expressions.
    payload = [pipeline_digest, source, formula_hints, title_context]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scientific_heading_reason(source, layout_label):
    """Leave a heading made entirely of identifiers in its conventional form."""
    if layout_label != "title":
        return ""
    text = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", source.strip())
    words = text.split()
    prose = {"A", "AN", "THE", "OF", "AND", "OR", "IN", "ON", "TO", "WITH", "FOR", "FROM", "BY",
             "AS", "AT", "DATA", "MODEL", "MODELS", "RESULT", "RESULTS", "METHOD", "METHODS", "TEST", "TESTS"}
    identifier = r"(?:[A-Z]{2,5}[0-9]*|[A-Z]{2,5}[a-z]{1,2}|[A-Z]{2,5}-[A-Z][a-z]+|I{1,3}[ab]?)"
    # Capitalization alone is insufficient: NEXT STEP is not a pair of names.
    anchor = any(re.search(r"\d|[a-z]|-", word) for word in words)
    short_acronyms = len(words) >= 2 and all(re.fullmatch(r"[A-Z]{2,3}", word) for word in words)
    if (words and not prose.intersection(words) and (anchor or short_acronyms)
            and all(re.fullmatch(identifier, word) for word in words)):
        return "科学缩写或名称保留原文"
    return ""


def publish_export(staging, folder):
    """Publish checked outputs together, rolling back a failed replacement."""
    staging, folder = Path(staging), Path(folder)
    names = ("translated.pdf", "bilingual.pdf", "quality.json")
    for name in names:
        if not (staging / name).is_file():
            raise FileNotFoundError(staging / name)
    previous = staging / "previous"
    previous.mkdir()
    for name in names:
        target = folder / name
        if target.exists():
            # A hard link retains the old bytes without duplicating large PDFs.
            try:
                os.link(target, previous / name)
            except OSError:
                shutil.copy2(target, previous / name)
    published = []
    try:
        for name in names:
            (staging / name).replace(folder / name)
            published.append(name)
    except BaseException:
        for name in reversed(published):
            backup, target = previous / name, folder / name
            if backup.exists():
                backup.replace(target)
            else:
                target.unlink(missing_ok=True)
        raise


def run(job_id, replay=False, layout_test=False, repair=False, missing_only=False, api_key=None):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("无效的论文编号")
    if not ready():
        raise RuntimeError("请先准备本地排版资源")
    folder = ROOT / "data" / "jobs" / job_id
    job = json.loads((folder / "job.json").read_text())
    settings = job["settings"]
    api_mode = settings.get("provider", "local") in {"deepseek", "api"} and not replay and not layout_test
    api_source = settings.get("provider", "local") in {"deepseek", "api"} and not layout_test
    if not replay and not layout_test and not WINDOWS and not api_mode:
        # MLX's process-wide GPU runtime must outlive BabelDOC's temporary
        # translation threads. Initialize it on the main thread, without
        # loading any weights; weights are still loaded only when needed.
        import mlx.core
        import mlx_lm
    initialize()
    if api_mode:
        # Layout, fonts and tokenization stay local. Only our explicit DeepSeek
        # transport can use the network; dependencies cannot download resources.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from .provider_network import block_worker_network
        block_worker_network()
    elif WINDOWS:
        from .gguf_inference import block_external_network
        block_external_network()
    else:
        block_network()
    from .layout_precision import install as preserve_image_precision
    preserve_image_precision()
    # Imports happen only after relocating the dependency's cache into the project.
    from babeldoc.translator.translator import BaseTranslator
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf import high_level
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.midend.layout_parser import LayoutParser
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    from babeldoc.format.pdf.document_il.utils import extract_char
    from babeldoc.progress_monitor import ProgressMonitor
    import pymupdf as fitz

    prior_pipeline = job.get("pipeline_version") or (job.get("export_report") or {}).get("pipeline_version") or "legacy"
    prior_code_digest = job.get("pipeline_code_sha256")
    code_hashes = {str(path.relative_to(CODE_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted([*(CODE_ROOT / "paperbridge").glob("*.py"),
                                       *(CODE_ROOT / "paperbridge" / "terminology").glob("*.json")])}
    code_digest = hashlib.sha256(json.dumps(code_hashes, sort_keys=True).encode()).hexdigest()
    job["pipeline_code_sha256"] = code_digest
    job["pipeline_code_files"] = code_hashes
    settings = job["settings"]
    sections = detect_sections(folder / "original.pdf")
    reference_regions = sections["reference_regions"]
    job["source_sections"] = sections
    first_reference = next((h for h in sections["section_headings"] if h["kind"] == "references"), None)
    job["reference_boundary"] = [first_reference["page"], first_reference["y"]] if first_reference else None
    job["pipeline_version"] = PIPELINE_VERSION
    with fitz.open(folder / "original.pdf") as original:
        image_boxes = {n + 1: [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
                       for n, page in enumerate(original)}
    model_dir = ROOT / "models" / ("qwen3-14b" if settings.get("profile") == "precision" else "qwen3-4b")
    if WINDOWS:
        from .windows_models import model_directory
        model_dir = model_directory(settings.get("profile", "standard"))
    if api_mode:
        from .providers import DeepSeekTranslator, DEFAULT_MODEL
        if settings.get("provider") == "api" and not settings.get("api_config"):
            raise RuntimeError("API 配置快照缺失，请在翻译服务中重新选择配置")
        local = DeepSeekTranslator(api_key, settings.get("api_model", DEFAULT_MODEL),
                                  folder / "translation-failures",
                                  cancelled=lambda: (folder / "cancel.request").exists(),
                                  config=settings.get("api_config") if settings.get("provider") == "api" else None)
    else:
        local = LocalTranslator(model_dir, folder / "translation-failures")
    if not replay and not layout_test and not local.installed():
        raise RuntimeError("选定的本地模型尚未准备完毕")
    glossary = terms_for(settings["domain"], settings["glossary"])
    source_profile, page_sizes = read_source_profile(folder / "original.pdf", sections)
    previous = {s["id"]: s for s in job["segments"]}
    previous_text = {(s["page"], s["source"]): s for s in job["segments"]}
    cache_path = folder / "paragraph-cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    quality_settings = {k: v for k, v in settings.items() if k not in {"pages", "auto_cleanup", "allow_slow_cpu", "force_cpu"}}
    installation = json.loads((model_dir / "installation.json").read_text()) if not api_source and local.installed() else {}
    digest = hashlib.sha256(json.dumps({"settings": quality_settings, "glossary": glossary,
                                       "model_revision": installation.get("revision"),
                                       "version": PIPELINE_VERSION, "code": code_digest}, sort_keys=True).encode()).hexdigest()
    records = {}
    paragraph_records = {}
    source_compositions = {}
    body_fonts = {}
    prepared = {}
    linked_results = {}
    provider_failure = None
    local_failure = None
    document_plan = None
    save_lock = threading.RLock()
    context = threading.local()
    job.update(status="running", progress=0, message="正在识别完整段落、行内公式和图表区域", cancel_requested=False)
    job["warnings"] = [w for w in job.get("warnings", []) if "结构检查发现需核对" not in w.get("message", "")]

    def save():
        with save_lock:
            job["segments"] = sorted(records.values(), key=lambda s: (s["page"], s["bbox"][1], s["bbox"][0]))
            write_json(folder / "worker-state.json", job)
            emit("state")

    if api_mode:
        job["api_usage"] = dict(local.inference.stats)
        def record_usage(usage):
            job["api_usage"] = usage
            save()
        local.inference.on_usage = record_usage
    elif not replay:
        job.pop("api_usage", None)
        def record_local_activity(state):
            job["local_runtime"] = state
            job["message"] = state["message"]
            save()
        if not layout_test:
            local.configure_runtime(settings, record_local_activity, lambda: (folder / "cancel.request").exists())

    def cancelled():
        return (folder / "cancel.request").exists()

    class OfflineAdapter(BaseTranslator):
        name = "paperbridge-gguf" if WINDOWS else "paperbridge-mlx"

        def __init__(self):
            super().__init__("en", "zh", ignore_cache=True)

        def get_formular_placeholder(self, placeholder_id):
            return "{v" + str(placeholder_id) + "}", r"\{v" + str(placeholder_id) + r"\}"

        def get_rich_text_left_placeholder(self, placeholder_id):
            return f"<b{placeholder_id}>", f"<b{placeholder_id}>"

        def get_rich_text_right_placeholder(self, placeholder_id):
            return f"</b{placeholder_id}>", f"</b{placeholder_id}>"

        def do_llm_translate(self, text, rate_limit_params=None):
            raise NotImplementedError

        def translate(self, text, ignore_cache=False, rate_limit_params=None):
            return self.do_translate(text, rate_limit_params)

        def do_translate(self, text, rate_limit_params=None):
            nonlocal provider_failure, local_failure
            if provider_failure:
                raise RuntimeError(provider_failure)
            seg = context.segment
            if cancelled():
                cfg.cancel_translation()
                raise TranslationError("已停止翻译")
            if seg["status"] == "preserved":
                return text
            seg["source"] = text
            seg["formula_hints"] = context.formula_hints
            key = translation_cache_key(digest, text, context.formula_hints, context.reference)
            seg["context_signature"] = signature(context.reference)
            seg["section_context"] = context.reference.get("当前章节", "")
            old = previous.get(seg["id"], {})
            effective_glossary = contextual_glossary(text, glossary, settings["domain"], context.formula_hints, context.reference)
            if (replay or repair) and old.get("source") != text:
                old = previous_text.get((seg["page"], text), {})
            literal = literal_reason(text, seg["layout_label"]) or scientific_heading_reason(text, seg["layout_label"])
            if old.get("status") != "edited" and (count_words(text) == 0 or literal):
                # Mathematical table headings can become recognizable only
                # after the engine has separated their formula components.
                seg.update(status="preserved", reason=literal or "公式或数学表头保留原文", translation="", notes=[])
                save()
                return text
            try:
                reused = False
                cache_reused = False
                same_formula_hints = old.get("formula_hints") is None or old["formula_hints"] == context.formula_hints
                reusable_automatic = repair and old.get("context_signature") == seg["context_signature"] and old.get("document_terms_signature") == signature(glossary) and old.get("source") == text and same_formula_hints and bool(old.get("translation")) and (missing_only or not has_issues(old, audit_translation(text, old["translation"], effective_glossary)))
                if reusable_automatic:
                    try:
                        validate_edit(text, old["translation"])
                    except ValueError:
                        reusable_automatic = False
                if old.get("status") == "edited" and old.get("source") == text:
                    validate_edit(text, old["translation"])
                    value, notes = old["translation"], ["用户已修改；科学含义请继续对照原文"]
                    seg["status"] = "edited"
                    reused = True
                elif key in cache and (not repair or missing_only or not has_issues(
                        {"source": text, **cache[key]}, audit_translation(text, cache[key]["translation"], effective_glossary))):
                    value, notes = cache[key]["translation"], cache[key]["notes"]
                    reused = cache_reused = True
                elif reusable_automatic:
                    value, notes = old["translation"], old.get("notes", [])
                    reused = True
                elif replay:
                    if old.get("source") == text and old.get("translation"):
                        value, notes = old["translation"], old.get("notes", [])
                        reused = True
                    else:
                        raise TranslationError("该段没有可重用的译文，保留英文")
                elif layout_test:
                    # Clearly labelled diagnostic run: identity, never a translation.
                    value, notes = text, ["排版诊断原文回放，未执行翻译"]
                else:
                    job["message"] = f"第 {seg['page']} 页 · 正在翻译完整段落（已生成 {sum(bool(s['translation']) for s in records.values())} 段）"
                    save()
                    group = document_plan.links.get(seg["id"])
                    if group and all(member in prepared and previous.get(member, {}).get("status") != "edited" for member in group["members"]) and (group["id"] in linked_results or all(records[member]["status"] == "pending" for member in group["members"])):
                        seg["continuation_group"] = group["id"]
                        if group["id"] not in linked_results:
                            parts = [{"id": member, **prepared[member]} for member in group["members"]]
                            try:
                                values = local.translate_linked(parts, context.reference, DOMAINS[settings["domain"]]["name"], glossary, settings["polish"])
                                linked_results[group["id"]] = values
                                job["document_context"]["joint_success"].append(group["id"])
                                for member in group["members"]:
                                    member_key = translation_cache_key(digest, prepared[member]["source"], prepared[member]["formula_hints"], document_plan.for_segment(records[member]))
                                    cache[member_key] = {"translation": values[member][0], "notes": values[member][1], "continuation_group": group["id"]}
                                write_json(cache_path, cache)
                            except TranslationError:
                                linked_results[group["id"]] = None
                                job["document_context"]["joint_fallback"].append(group["id"])
                        values = linked_results[group["id"]]
                    else:
                        values = None
                    if values:
                        value, notes = values[seg["id"]]
                    else:
                        value, notes = local.translate(text, context.reference, DOMAINS[settings["domain"]]["name"], glossary, settings["polish"], context.formula_hints, footnote=seg.get("footnote", False))
                        if group and linked_results.get(group["id"], True) is None:
                            notes += ["续接联合译文未通过完整性检查，采用相邻语境逐段翻译，请核对衔接"]
                    cache[key] = {"translation": value, "notes": notes, "continuation_group": group["id"] if group and values else None}
                    write_json(cache_path, cache)
                # Layout warnings are recomputed on every export, rather than
                # carried forward from an earlier output or check implementation.
                notes = [note for note in notes if not note.startswith(("公式组件检查发现差异", "PDF 中未确认完整译文"))]
                seg.update(translation=value, notes=notes)
                seg["translation_pipeline_version"] = old.get("translation_pipeline_version", prior_pipeline) if reused and not cache_reused else PIPELINE_VERSION
                seg["translation_code_sha256"] = old.get("translation_code_sha256", prior_code_digest) if reused and not cache_reused else code_digest
                seg["translation_reused"] = reused
                seg["document_terms_signature"] = old.get("document_terms_signature") if reused and not cache_reused else signature(glossary)
                if key in cache and cache[key].get("continuation_group"):
                    group_id = cache[key]["continuation_group"]
                    seg["continuation_group"] = group_id
                    if group_id not in job["document_context"]["joint_success"]:
                        job["document_context"]["joint_success"].append(group_id)
                if not layout_test:
                    seg["translation_audit"] = audit_translation(text, value, effective_glossary)
                    seg["notes"] = list(dict.fromkeys(notes + ["自动复核疑点：" + item["message"] for item in seg["translation_audit"]]))
                if seg["status"] != "edited":
                    seg["status"] = "review" if has_issues(seg, seg.get("translation_audit", [])) else "translated"
            except Exception as exc:
                from .providers import ProviderError
                if isinstance(exc, (ProviderError, LocalRuntimeError)):
                    if isinstance(exc, LocalRuntimeError):
                        local_failure = exc
                        job["failure"] = exc.public()
                    provider_failure = str(exc)
                    cfg.cancel_translation()
                    job["message"] = provider_failure
                    save()
                    raise
                if not isinstance(exc, TranslationError):
                    raise
                seg.update(status="failed", reason=str(exc), translation="", notes=[])
                value = text
            save()
            return value

    class TrackingTranslator(ILTranslator):
        @staticmethod
        def formula_hints(translate_input):
            hints = (translate_input.get_placeholders_hint() or {}) if translate_input else {}
            if translate_input:
                for placeholder in translate_input.placeholders:
                    tag = getattr(placeholder, 'placeholder', None)
                    formula = getattr(placeholder, 'formula', None)
                    if tag in hints and formula and has_numeric_scripts(formula):
                        hints[tag] = readable_numeric_hint(formula)
            return hints

        def pre_translate_paragraph(self, *args, **kwargs):
            text, translate_input = super().pre_translate_paragraph(*args, **kwargs)
            context.formula_hints = self.formula_hints(translate_input)
            return text, translate_input

        def translate(self, docs):
            nonlocal document_plan, glossary, digest
            # BabelDOC has finished layout inference for all selected pages.
            # No layout session is needed while the large language model runs.
            import gc
            layout_model.model = None
            gc.collect()
            for p in docs.page:
                edited_sources = {old["source"] for old in previous.values()
                                  if old["page"] == p.page_number + 1 and old["status"] == "edited"}
                p.pdf_paragraph = merge_edge_fragments(p.pdf_paragraph, edited_sources)
            job["table_count"] = sum(layout.class_name == "table" for p in docs.page for layout in p.page_layout)
            job["parsed_paragraph_count"] = sum(len(p.pdf_paragraph) for p in docs.page)
            for p in docs.page:
                body_fonts[p.page_number] = dominant_body_font(p.pdf_paragraph)
                fonts = {font.font_id: font for font in p.pdf_font}
                xobj_fonts = {xobj.xobj_id: {**fonts, **{font.font_id: font for font in xobj.pdf_font}} for xobj in p.pdf_xobject}
                for para in p.pdf_paragraph:
                    seg = segment_for(para, p)
                    records[seg["id"]] = seg
                    paragraph_records[id(para)] = seg
                    source_compositions[id(para)] = snapshot_graph(para.pdf_paragraph_composition)
                    if seg["status"] != "preserved":
                        try:
                            translate_input = self.get_translate_input(para, xobj_fonts.get(para.xobj_id, fonts), disable_rich_text_translate=True)
                        except Exception as exc:
                            seg.update(status="failed", reason=f"公式组合未能安全处理（{type(exc).__name__}），保留英文")
                            continue
                        if translate_input:
                            seg["source"] = translate_input.unicode
                            seg["formula_hints"] = self.formula_hints(translate_input)
                            prepared[seg["id"]] = {"source": seg["source"], "formula_hints": seg["formula_hints"]}
            save()
            try:
                table_regions = [{"page": p.page_number + 1,
                                  "bbox": [layout.box.x, p.cropbox.box.y2-layout.box.y2,
                                           layout.box.x2, p.cropbox.box.y2-layout.box.y]}
                                 for p in docs.page for layout in p.page_layout if layout.class_name == "table"]
                document_plan = DocumentContext(list(records.values()), sections, page_sizes, source_profile, table_regions)
                glossary = contextual_glossary("", glossary, settings["domain"], reference={
                    "论文标题": document_plan.title, "摘要背景": document_plan.abstract})
                term_key = signature({"source": hashlib.sha256((folder / "original.pdf").read_bytes()).hexdigest(),
                                      "domain": settings["domain"], "base": glossary,
                                      "provider": settings.get("provider", "local"), "api_model": settings.get("api_model") if api_source else None,
                                      "api_config": settings.get("api_config") if api_source else None,
                                      "model_revision": installation.get("revision"), "version": CONTEXT_VERSION, "code": code_digest})
                old_terms = job.get("document_terminology", {})
                proposals = [e for e in old_terms.get("suggestions", old_terms.get("entries", [])) if e.get("origin") == "model"] if old_terms.get("signature") == term_key else []
                notes = []
                if old_terms.get("signature") != term_key and not replay and not layout_test and not cancelled():
                    job["message"] = "正在结合全文语境整理本篇专业术语"
                    save()
                    candidates = term_candidates(source_profile, glossary, limit=12)
                    try:
                        proposals = local.extract_terms(candidates, {"标题": document_plan.title, "摘要": document_plan.abstract}, DOMAINS[settings["domain"]]["name"])
                    except TranslationError as exc:
                        notes.append("自动术语建议未生成，使用已有专业词表：" + str(exc))
                proposals, conflicts = consistent_term_proposals(proposals, glossary)
                if conflicts:
                    notes.append("已按专业词库约束排除 " + str(len(conflicts)) + " 条有冲突的自动术语建议")
                entries = glossary_entries(source_profile, glossary, proposals, job.get("document_term_overrides", ""))
                job["document_terminology"] = {"version": CONTEXT_VERSION, "signature": term_key, "entries": entries,
                                              "suggestions": proposals, "notes": notes, "conflicts": conflicts,
                                              "model_suggestions": len(proposals), "semantic_review": "pending"}
                write_json(folder / "document-terminology.json", job["document_terminology"])
                glossary = active_document_terms(job, glossary)
                digest = signature([digest, glossary, CONTEXT_VERSION])
                job["document_context"] = document_plan.summary()
                if cancelled():
                    cfg.cancel_translation()
                save()
                super().translate(docs)
            finally:
                # Chinese text is now saved; font embedding/export does not
                # need the model, tokenizer or response KV buffers.
                local.unload()
            # Paragraphs that BabelDOC could not translate must remain visible.
            if not cancelled():
                for seg in records.values():
                    if seg["status"] == "pending":
                        seg.update(status="failed", reason="段落识别或公式组合未能安全处理，保留英文")
            save()

        def translate_paragraph(self, paragraph, page, *args, **kwargs):
            seg = paragraph_records[id(paragraph)]
            context.segment = seg
            context.reference = document_plan.for_segment(seg)
            if context.reference.get("表格语境"):
                seg["table_context"] = context.reference["表格语境"]
            if seg["status"] in {"preserved", "failed"}:
                if args and args[0]:
                    args[0].advance()
                return
            super().translate_paragraph(paragraph, page, *args, **kwargs)

    class CompleteLineLayout(LayoutParser):
        def generate_fallback_line_layout_for_page(self, page, progress):
            # Detected rectangles sometimes clip the N in 'Note' or the final n
            # in 'rewritten'. Expand only to a nearly covered physical line,
            # and never bridge a column gutter or a figure boundary.
            boxes = extract_char.convert_page_to_char_boxes(page)
            for line in extract_char.process_page_chars_to_lines(boxes) if boxes else []:
                chars = [item[0] for item in line.chars]
                x0, x1 = min(b.x for b in chars), max(b.x2 for b in chars)
                y0, y1 = min(b.y for b in chars), max(b.y2 for b in chars)
                for layout in page.page_layout:
                    if layout.class_name not in {"plain text", "title", "figure_caption", "table_caption"}:
                        continue
                    b = layout.box
                    height = max(1, y1 - y0)
                    vertical_overlap = max(0, min(y1, b.y2) - max(y0, b.y)) / height
                    covered = sum(b.x <= (c.x + c.x2) / 2 <= b.x2 for c in chars) / len(chars)
                    allowance = 4 * height if layout.class_name == "title" else 2 * height
                    if vertical_overlap > .6 and covered > .75 and x1 - x0 <= b.x2 - b.x + allowance:
                        b.x, b.x2 = min(b.x, x0), max(b.x2, x1)
                        b.y, b.y2 = min(b.y, y0), max(b.y2, y1)
                        break
            return super().generate_fallback_line_layout_for_page(page, progress)

    class PreserveOriginalTypesetting(Typesetting):
        def render_page(self, page):
            result = super().render_page(page)
            def characters(paragraph):
                return [c.pdf_character for c in paragraph.pdf_paragraph_composition if c.pdf_character]
            for fragment in page.pdf_paragraph:
                small = paragraph_records.get(id(fragment))
                if not small or small.get('reason') != '科学对象编号保留原文':
                    continue
                for parent in page.pdf_paragraph:
                    large = paragraph_records.get(id(parent))
                    if not large or not large.get('translation'):
                        continue
                    if not identifier_suffix(small['source'], small['bbox'], [(large['source'], large['bbox'])]):
                        continue
                    year = re.search(r'[12]\d{3}\s*$', large['source'])
                    if not year or not large['translation'].rstrip().endswith(year[0].strip()):
                        continue
                    first, last = characters(fragment), characters(parent)
                    if not first or not last:
                        continue
                    dx, dy = last[-1].box.x2 - first[0].box.x, last[-1].box.y - first[0].box.y
                    for char in first:
                        for box in [char.box, getattr(getattr(char, 'visual_bbox', None), 'box', None)]:
                            if box is not None:
                                box.x, box.x2 = box.x + dx, box.x2 + dx
                                box.y, box.y2 = box.y + dy, box.y2 + dy
                    break
            return result

        def render_paragraph(self, paragraph, page, fonts):
            seg = paragraph_records.get(id(paragraph))
            def formula_inventory(comps):
                return Counter((c.char_unicode, c.pdf_character_id, c.pdf_style.font_id if c.pdf_style else None)
                               for comp in comps if comp.pdf_formula for c in comp.pdf_formula.pdf_character
                               if c.char_unicode.strip())
            def rendered_inventory(comps):
                chars = [comp.pdf_character for comp in comps if comp.pdf_character]
                chars += [c for comp in comps if comp.pdf_formula for c in comp.pdf_formula.pdf_character]
                return Counter((c.char_unicode, c.pdf_character_id, c.pdf_style.font_id if c.pdf_style else None)
                               for c in chars if c.char_unicode.strip())
            original_formulas = formula_inventory(source_compositions.get(id(paragraph), []))
            if seg and not re.search(r"[\u3400-\u9fff]", seg.get("translation", "")):
                # Reuse original positions for references, figure labels,
                # displayed formulas, and failed translations. Formula groups
                # also retain their vector strokes and graphics.
                comps = []
                for comp in source_compositions[id(paragraph)]:
                    if comp.pdf_formula or comp.pdf_character:
                        comps.append(comp)
                    elif comp.pdf_line:
                        comps.extend(PdfParagraphComposition(pdf_character=c) for c in comp.pdf_line.pdf_character)
                    elif comp.pdf_same_style_characters:
                        comps.extend(PdfParagraphComposition(pdf_character=c) for c in comp.pdf_same_style_characters.pdf_character)
                    else:
                        comps.append(comp)
                paragraph.pdf_paragraph_composition = comps
                paragraph.scale = 1.0
                seg["formula_components_preserved"] = not bool(original_formulas - rendered_inventory(comps))
                return
            lines = split_unicode_lines(paragraph.pdf_paragraph_composition) if seg and (paragraph.layout_label == 'title' or seg.get('status') == 'edited') and not original_formulas else None
            if lines:
                rendered, scales = [], []
                height = (paragraph.box.y2 - paragraph.box.y) / len(lines)
                for index, compositions in enumerate(lines):
                    child = snapshot_graph(paragraph)
                    child.pdf_paragraph_composition = compositions
                    child.box.y2 = paragraph.box.y2 - index * height
                    child.box.y = child.box.y2 - height
                    units = self.create_typesetting_units(child, fonts)
                    child.optimal_scale = self._get_optimal_scale(child, page, units)
                    super().render_paragraph(child, page, fonts)
                    rendered.extend(child.pdf_paragraph_composition)
                    scales.append(child.scale or 1)
                paragraph.pdf_paragraph_composition = rendered
                paragraph.scale = min(scales)
                self._update_paragraph_render_order(paragraph)
                result = None
            else:
                result = super().render_paragraph(paragraph, page, fonts)
            if seg:
                # Reflow flattens PdfFormula into PdfCharacter records; compare
                # retained original glyphs, rather than requiring the old wrapper.
                seg["formula_components_preserved"] = not bool(original_formulas - rendered_inventory(paragraph.pdf_paragraph_composition))
                if not seg["formula_components_preserved"]:
                    seg["notes"].append("公式组件检查发现差异，请核对本段公式")
            return result

    def segment_for(para, page):
        b = para.box
        height = page.cropbox.box.y2
        box = [round(b.x, 3), round(height - b.y2, 3), round(b.x2, 3), round(height - b.y, 3)]
        source = para.unicode or ""
        footnote = is_natural_footnote(source, box, para.layout_label, height,
                                      font_size=para.pdf_style.font_size if para.pdf_style else None,
                                      body_font_size=body_fonts.get(page.page_number))
        number = page.page_number + 1
        sid = "p" + str(number) + "-" + hashlib.sha256(json.dumps([number, box, source]).encode()).hexdigest()[:12]
        reason = ""
        rectangle = fitz.Rect(box)
        figure_boxes = [fitz.Rect(layout.box.x, height - layout.box.y2, layout.box.x2, height - layout.box.y)
                        for layout in page.page_layout if layout.class_name == "figure"]
        in_figure = any((rectangle & region).get_area() > .5 * max(1, rectangle.get_area())
                        for region in figure_boxes + image_boxes.get(number, []))
        if is_reference_region(number, box, reference_regions):
            reason = "参考文献保留原文"
        elif para.vertical:
            reason = "旋转文字保留原文"
        elif in_figure:
            reason = "图内文字保留原文"
        elif para.pdf_paragraph_composition and all(c.pdf_formula is not None for c in para.pdf_paragraph_composition):
            reason = "独立公式保留原文"
        elif frontmatter_reason(source, number, box, height, layout_label=para.layout_label):
            reason = frontmatter_reason(source, number, box, height, layout_label=para.layout_label)
        elif count_words(source) == 0:
            reason = "公式、数值或页码保留原文"
        elif para.layout_label in {"abandon", "figure"} and not footnote:
            reason = "页边或图内文字保留原文"
        elif para.layout_label == 'fallback_line' and identifier_suffix(
                source, [b.x, b.y, b.x2, b.y2],
                [(other.unicode or '', [other.box.x, other.box.y, other.box.x2, other.box.y2])
                 for other in page.pdf_paragraph if other is not para]):
            reason = "科学对象编号保留原文"
        elif literal_reason(source, para.layout_label):
            reason = literal_reason(source, para.layout_label)
        elif scientific_heading_reason(source, para.layout_label):
            reason = scientific_heading_reason(source, para.layout_label)
        if reason in {"作者姓名保留原文", "作者机构与通讯地址保留原文", "作者引文与学术名称保留原文", "公式函数或单位保留原文", "科学对象编号保留原文", "科学缩写或名称保留原文", "探测器或科学模型名称保留原文"} and previous.get(sid, {}).get("status") == "edited":
            reason = ""  # Honor a saved user translation of literal metadata.
        source_xref = next((x.xref_id for x in page.pdf_xobject if x.xobj_id == para.xobj_id), None)
        return {"id": sid, "page": number, "source": source, "translation": "", "bbox": box,
                "source_xref": source_xref,
                "font_size": para.pdf_style.font_size if para.pdf_style else 10,
                "kind": "paragraph", "layout_label": para.layout_label,
                "footnote": footnote,
                "status": "preserved" if reason else "pending", "reason": reason, "notes": [], "exported": False}

    title = " ".join(s["source"] for s in job["segments"] if s["page"] == 1)[:700]
    protected_numeric_scripts = []
    def record_numeric_script(number, formula):
        protected_numeric_scripts.append({"page": number,
                                          "glyph_text": "".join(c.char_unicode or "" for c in formula.pdf_character)})
    high_level.StylesAndFormulas = numeric_script_parser(
        high_level.StylesAndFormulas,
        [(s["page"], s["bbox"]) for s in previous.values() if s["status"] == "edited"],
        record_numeric_script)
    high_level.ILTranslator = TrackingTranslator
    high_level.LayoutParser = CompleteLineLayout
    high_level.Typesetting = PreserveOriginalTypesetting
    pages = settings.get("pages", "")
    if not pages and sections["body_pages"]:
        pages = ",".join(str(number) for number in sections["body_pages"])
    work = folder / "layout-work"
    work.mkdir(exist_ok=True)
    adapter = OfflineAdapter()
    # Keep the identical static detection graph but avoid CoreML's native mutex
    # teardown failure observed after successful exports. Inference remains local.
    from unittest.mock import patch
    import onnxruntime
    original_session = onnxruntime.InferenceSession
    def cpu_session(*args, **kwargs):
        kwargs["providers"] = ["CPUExecutionProvider"]
        return original_session(*args, **kwargs)
    with patch.object(onnxruntime, "InferenceSession", cpu_session):
        layout_model = OnnxModel.from_pretrained()
    cfg = TranslationConfig(translator=adapter, input_file=folder / "original.pdf", lang_in="en", lang_out="zh",
                            doc_layout_model=layout_model, output_dir=work, working_dir=work,
                            pages=pages or None, debug=False, use_rich_pbar=False, pool_max_workers=1,
                            qps=1, auto_extract_glossary=False, save_auto_extracted_glossary=False,
                            watermark_output_mode=WatermarkOutputMode.NoWatermark,
                            no_dual=True,
                            use_alternating_pages_dual=True, min_text_length=2,
                            skip_clean=True, enable_graphic_element_process=True)

    def progress(**event):
        if cancelled():
            cfg.cancel_translation()
        if "overall_progress" in event:
            job["progress"] = round(min(98, event["overall_progress"]), 1)
            if "Translate" not in event.get("stage", ""):
                job["message"] = "正在识别段落与公式、排版并检查页面"
            save()

    pending_export = tempfile.TemporaryDirectory(prefix="pending-export-", dir=work)
    staging = Path(pending_export.name)
    previous_report = job.get("export_report")
    output_published = False
    try:
        started = time.time()
        with ProgressMonitor(high_level.get_translation_stage(cfg), progress_change_callback=progress,
                             finish_callback=lambda **event: None, report_interval=2,
                             cancel_event=threading.Event()) as monitor:
            result = high_level.do_translate(monitor, cfg)
        if provider_failure:
            if local_failure:
                raise local_failure
            from .providers import ProviderError
            raise ProviderError(provider_failure)
        job["protected_numeric_scripts"] = protected_numeric_scripts
        if cancelled():
            raise TranslationError("已停止，未发布新的 PDF")
        # Keep the previous output readable until every export step succeeds.
        translated_path = staging / "translated.pdf"
        shutil.copyfile(result.mono_pdf_path, translated_path)
        from .graphics import restore_untouched_forms, make_alternating_pdf
        job["restored_figure_forms"] = restore_untouched_forms(folder / "original.pdf", translated_path, list(records.values()))
        from .reference_restore import restore_reference_text
        job["restored_reference_regions"] = restore_reference_text(folder / "original.pdf", translated_path, reference_regions)
        from .pdf_compaction import compact_pdf
        compression = compact_pdf(translated_path)
        make_alternating_pdf(folder / "original.pdf", translated_path, staging / "bilingual.pdf")
        with fitz.open(translated_path) as translated:
            page_chinese = {}
            for seg in records.values():
                if seg["translation"] and not layout_test:
                    chinese = "".join(re.findall(r"[\u3400-\u9fff]", seg["translation"]))
                    if seg['page'] not in page_chinese:
                        page_chinese[seg['page']] = "".join(re.findall(r"[\u3400-\u9fff]", translated[seg['page'] - 1].get_text()))
                    page_text = page_chinese[seg['page']]
                    # Verify emitted Chinese against PDF text, not inference alone.
                    seg["exported"] = bool(chinese) and chinese in page_text and seg.get("formula_components_preserved", True)
                    if not seg["exported"]:
                        seg["notes"].append("PDF 中未确认完整译文，请核对版面；段落译文仍保存在记录中")
                if seg["status"] in {"translated", "review"}:
                    seg["status"] = "review" if has_issues(seg, seg.get("translation_audit", [])) else "translated"
            geometry_ok = len(translated) == job["page_count"]
            candidate = [s for s in records.values() if s["status"] != "preserved" and count_words(s["source"]) >= 3]
            done = [s for s in candidate if s["exported"]]
            words = sum(count_words(s["source"]) for s in candidate)
            covered = sum(count_words(s["source"]) for s in done)
            job["export_report"] = {"engine": "BabelDOC 0.6.4 + Model API" if api_source else "BabelDOC 0.6.4 + local llama.cpp" if WINDOWS else "BabelDOC 0.6.4 + direct MLX", "pipeline_version": PIPELINE_VERSION, "layout_test": layout_test,
                                    "page_count": len(translated), "original_page_count": job["page_count"],
                                    "page_count_preserved": geometry_ok,
                                    "natural_language_paragraphs": len(candidate), "exported_paragraphs": len(done),
                                    "paragraph_coverage_percent": round(100 * len(done) / max(1, len(candidate)), 1),
                                    "source_word_coverage_percent": round(100 * covered / max(1, words), 1),
                                    "coverage_definition": "选定页面中至少含三个英文词的自然语言段落（含文字脚注）；不含参考文献、图内文字、纯公式、学术名称、作者机构地址和页眉页脚；以PDF实际文字提取确认",
                                    "elapsed_seconds": round(time.time() - started, 1), "pdf_compression": compression, "semantic_review": "pending",
                                    "inference_performance": job.get("api_usage", {}) if api_source and replay else local.inference.stats,
                                    "document_terminology_count": len(job.get("document_terminology", {}).get("entries", [])),
                                    "document_context": job.get("document_context", {}),
                                    "resource_policy": {"release_layout_before_inference": True,
                                                        "release_llm_before_export": True,
                                                        "native_layout_statistics_disabled": True,
                                                        "single_bilingual_export": True},
                                    "repair_run": repair,
                                    "repair_scope": "missing_only" if missing_only else "issues_and_missing" if repair else "none",
                                    "pipeline_code_sha256": code_digest,
                                    "model_revision": installation.get("revision"),
                                    "reused_translation_count": sum(s.get("translation_reused", False) for s in records.values()),
                                    "translation_versions": dict(Counter(s.get("translation_pipeline_version", "unknown") for s in records.values() if s["translation"])),
                                    "translation_code_versions": dict(Counter(s.get("translation_code_sha256") or "legacy/not-recorded" for s in records.values() if s["translation"])),
                                    "automatic_audit_issue_count": sum(len(s.get("translation_audit", [])) for s in records.values()),
                                    "formula_components_preserved": all(s.get("formula_components_preserved", True) for s in records.values()),
                                    "provider": settings.get("provider", "local"),
                                    "api_model": settings.get("api_model") if api_source else None,
                                    "model_api": api_source, "api_requests_this_run": api_mode,
                                    "network_blocked_during_translation": not api_mode}
        from .quality import inspect_pdf
        evidence = inspect_pdf(folder / "original.pdf", translated_path, list(records.values()))
        write_json(staging / "quality.json", evidence)
        job["export_report"]["structural_checks_passed"] = evidence["structural_checks_passed"]
        job["export_report"]["structural_review_pages"] = evidence.get("failed_pages", [])
        if not evidence["structural_checks_passed"]:
            job["warnings"].append({"page": 0, "message": "结构检查发现需核对的页面：" + ", ".join(map(str, evidence.get("failed_pages", [])))})
        if cancelled():
            raise TranslationError("已停止，未发布新的 PDF")
        publish_export(staging, folder)
        output_published = True
        job.update(status="complete", progress=100, has_output=True)
        job["message"] = completion_message(sum(has_issues(s, s.get("translation_audit", [])) for s in records.values())) if not layout_test else "排版诊断已完成，未执行翻译"
        save()
    except BaseException as exc:
        # BabelDOC can surface its cancellation after a terminal engine error.
        # Keep the original model failure as the task's diagnostic and exit.
        if local_failure:
            exc = local_failure
        if isinstance(exc, LocalRuntimeError):
            job["failure"] = exc.public()
        if not output_published:
            job["export_report"] = previous_report
        job.update(status="cancelled" if cancelled() else "failed",
                   message="已停止，完成的段落已保存，可继续" if cancelled() else f"全文处理失败：{provider_failure or str(exc)[:200]}")
        save()
        if local_failure:
            raise local_failure from None
        raise
    finally:
        local.unload()
        pending_export.cleanup()


def worker_main():
    if os.environ.get("PAPERBRIDGE_TRACE"):
        import faulthandler
        faulthandler.dump_traceback_later(45, repeat=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--layout-test", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--repair-missing", action="store_true")
    parser.add_argument("--provider-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    if sum([args.replay, args.layout_test, args.repair, args.repair_missing]) > 1:
        parser.error("--replay、--layout-test、--repair、--repair-missing 请只选择一种")
    exit_code = 0
    try:
        secret = json.loads(sys.stdin.readline(2048)).get("api_key") if args.provider_stdin else None
        run(args.job_id, args.replay, args.layout_test, args.repair or args.repair_missing, args.repair_missing, secret)
    except BaseException:
        import traceback
        traceback.print_exc()
        exit_code = 1
    # This is a dedicated child process. All result files, checks and final
    # worker state are closed/atomically saved by run() before success returns.
    # macOS ONNX Runtime 1.30's background Events SDK can access a destroyed
    # mutex during interpreter teardown even after telemetry has been disabled.
    # Let the OS reclaim this isolated process instead of running unsafe native
    # global destructors. Exceptions still produce nonzero exit and failed state.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


if __name__ == "__main__":
    worker_main()
