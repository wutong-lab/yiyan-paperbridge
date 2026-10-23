import asyncio
import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from stat import S_ISREG
from urllib.parse import unquote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from .glossary import DOMAINS, terms_for, contextual_glossary
from .pdf_engine import analyze, export_pdf, render_page
from .translator import ROOT, MODEL_DIR, MODEL_ID, TranslationError, translator
from .layout_runtime import ready as layout_ready
from .translation_audit import audit_translation
from .terminology import terminology_entries
from .document_context import glossary_entries, active_document_terms
from .glossary import parse_custom
from .storage_cleanup import cleanup_after_task
from .resources import memory_usage, release_idle_memory
from .storage import disk_usage, storage_inventory, clear_optional_files, safe_path, download_cache_guard
from .review import reading_segments, issue_notes, completion_message

from .paths import CODE_ROOT, WINDOWS, hidden_process
from .providers import credentials, DeepSeekClient, APIClient, MODELS, DEFAULT_MODEL, ProviderError
from .api_profiles import ProfileManager
from .table_policy import apply_table_policy
from .secret_store import SecretStoreError
from .local_runtime import assess_local, require_local, LocalRuntimeError, RuntimeWatchdog, ADVICE
from .annotations import (AnnotationCreate, AnnotationError, add_annotation, delete_annotation,
                          list_annotations, page_words, export_annotations)

api_profiles = ProfileManager(ROOT)

PRECISION_DIR = ROOT / "models" / "qwen3-14b"
if WINDOWS:
    from .windows_models import model_directory
    PRECISION_DIR = model_directory("precision")

DATA = ROOT / "data" / "jobs"
DATA.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="译研 PaperBridge", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paperbridge-local")
state_lock = threading.RLock()
jobs = {}
active_operations = set()
upload_tasks = set()
install_state = {"status": "idle", "message": "", "bytes": 0}


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    from fastapi.responses import JSONResponse
    # Pydantic normally echoes invalid input values, including an entered key.
    # Return only locations and validation messages for every settings endpoint.
    errors = [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
              for item in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def local_only(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get("host"):
            return Response("只允许本机软件页面访问", status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return Response("只允许本机软件页面访问", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
    return response


def save_job(job):
    with state_lock:
        path = DATA / job["id"] / "job.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def get_job(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "未找到该论文")
    with state_lock:
        if job_id not in jobs:
            path = DATA / job_id / "job.json"
            if not path.exists():
                raise HTTPException(404, "未找到该论文")
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] in {"running", "queued", "exporting"}:
                job["status"] = "interrupted"
                job["message"] = "上次运行已中断，可继续翻译"
            jobs[job_id] = job
        return jobs[job_id]


def job_summary(job):
    result = {k: v for k, v in job.items() if k not in {"segments", "metadata"}}
    # Rendering can change after a manual paragraph edit without a new translation
    # timer. Let readers invalidate PDF and annotation caches on the actual file.
    result["output_revision"] = ""
    if job.get("has_output"):
        try:
            info = (DATA / job["id"] / "translated.pdf").stat()
            if S_ISREG(info.st_mode):
                result["output_revision"] = f"{info.st_mtime_ns}:{info.st_size}:{info.st_ino}"
        except OSError:
            pass
    result["translated"] = sum(bool(s["translation"]) for s in job["segments"])
    result["exported"] = sum(s.get("exported", False) for s in job["segments"])
    checked = reading_segments(job)
    result["review_count"] = result["issue_count"] = sum(s["has_issues"] for s in checked)
    result["automatic_check"] = "diagnostic" if (job.get("export_report") or {}).get("layout_test") else "complete" if job.get("status") == "complete" else "pending"
    if result["automatic_check"] == "complete" and job.get("message", "").startswith(("全文段落排版已生成", "翻译完成，有", "翻译与排版已完成")):
        result["message"] = completion_message(result["issue_count"])
    result["preserved_count"] = sum(s["status"] == "preserved" for s in job["segments"])
    result["candidates"] = sum(s["status"] != "preserved" for s in job["segments"])
    timing = result.get("timing")
    if timing and timing.get("started_at") and not timing.get("finished_at"):
        result["timing"] = {**timing, "elapsed_seconds": round(max(0, time.time() - timing["started_at"]), 1)}
    return copy.deepcopy(result)


def start_timing(job, kind):
    job["timing"] = {"kind": kind, "started_at": time.time()}
    return time.monotonic()


def finish_timing(job, started):
    job["timing"].update(finished_at=time.time(), elapsed_seconds=round(max(0, time.monotonic() - started), 1),
                         status=job["status"])
    if job["timing"]["kind"] in {"translation", "repair"} and job["status"] == "complete":
        job["last_translation_timing"] = copy.deepcopy(job["timing"])


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    files = list(MODEL_DIR.glob("*.gguf" if WINDOWS else "*.safetensors"))
    with state_lock:
        processing = bool(active_operations) or any(j["status"] in {"queued", "running", "exporting"} for j in jobs.values())
        can_cleanup = not processing and install_state["status"] != "running"
    result = {"model_installed": translator.installed(), "model_loaded": translator.model is not None,
            "memory": memory_usage(), "can_cleanup": can_cleanup,
            "disk": disk_usage(ROOT),
            "model": MODEL_ID, "model_bytes": sum(p.stat().st_size for p in files),
            "installation": copy.deepcopy(install_state), "offline": True,
            "layout_installed": layout_ready(),
            "issue_navigation": True,
            "repair_available": True,
            "precision_installed": precision_installed(),
            "precision_model": "Qwen/Qwen3-14B-GGUF" if WINDOWS else "Qwen/Qwen3-14B-MLX-4bit",
            "domains": [{"id": k, "name": v["name"]} for k, v in DOMAINS.items()]}
    result["platform"] = "windows" if WINDOWS else "macos"
    result["app_version"] = os.environ.get("PAPERBRIDGE_APP_VERSION", "development")
    result["deepseek"] = credentials.status()
    try:
        result["api_profiles"] = api_profiles.status()
    except ValueError as exc:
        result["api_profiles"] = {"profiles": [], "error": str(exc)}
    if WINDOWS:
        from .windows_models import hardware, default_profile
        result["hardware"] = hardware()
        result["default_profile"] = default_profile()
        result["bundled_models"] = [p for p in ("standard", "precision")
                                    if model_directory(p).parent.name == "Models"]
    else:
        try:
            marker = json.loads((ROOT / "macos-installation.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            marker = {}
        preferred = marker.get("default_profile") if isinstance(marker, dict) else None
        available = {"standard": result["model_installed"], "precision": result["precision_installed"]}
        result["default_profile"] = preferred if available.get(preferred) else ("precision" if available["precision"] else "standard")
        result["managed_models"] = bool(marker.get("models_managed_separately")) if isinstance(marker, dict) else False
    return result


class APIKeySettings(BaseModel):
    model_config = {"extra": "forbid"}
    api_key: str = Field(min_length=8, max_length=512)


class APIProfileSettings(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(max_length=80)
    kind: str = Field(default="custom", max_length=30)
    base_url: str = Field(max_length=1000)
    model: str = Field(max_length=200)
    api_key: str = Field(default="", max_length=512)
    json_mode: bool = False
    stream_usage: bool = False


def profile_action(action):
    try:
        return action()
    except (ValueError, SecretStoreError, ProviderError) as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError:
        raise HTTPException(409, "无法保存或读取 API 配置，请检查软件数据目录的访问权限") from None


@app.get("/api/providers/profiles")
def list_api_profiles():
    return profile_action(api_profiles.status)


@app.post("/api/providers/profiles")
def create_api_profile(settings: APIProfileSettings):
    return profile_action(lambda: api_profiles.save(settings.model_dump()))


@app.put("/api/providers/profiles/{profile_id}")
def update_api_profile(profile_id: str, settings: APIProfileSettings):
    return profile_action(lambda: api_profiles.save(settings.model_dump(), profile_id))


@app.delete("/api/providers/profiles/{profile_id}")
def delete_api_profile(profile_id: str):
    profile_action(lambda: api_profiles.delete(profile_id))
    return {"deleted": True}


@app.post("/api/providers/profiles/{profile_id}/test")
def test_api_profile(profile_id: str):
    def test():
        config, key = api_profiles.resolve(profile_id)
        return APIClient(key, config).test_connection()
    return profile_action(test)


@app.put("/api/providers/deepseek/key")
def set_deepseek_key(settings: APIKeySettings):
    try:
        credentials.set(settings.api_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return credentials.status()


@app.delete("/api/providers/deepseek/key")
def clear_deepseek_key():
    credentials.clear()
    return credentials.status()


@app.post("/api/providers/deepseek/test")
def test_deepseek_connection(model: str = DEFAULT_MODEL):
    if model not in MODELS:
        raise HTTPException(400, "未知的 DeepSeek 模型")
    try:
        return DeepSeekClient(credentials.get(), model).test_connection()
    except ProviderError as exc:
        raise HTTPException(409, str(exc)) from None


def precision_installed():
    if WINDOWS:
        from .windows_models import installed
        return installed(PRECISION_DIR)
    return (PRECISION_DIR / "installation.json").is_file()


@app.get("/api/desktop/status")
def desktop_status():
    """Cheap identity and activity probe for the native desktop application."""
    with state_lock:
        busy = (bool(active_operations) or install_state["status"] == "running"
                or any(job["status"] in {"queued", "running", "exporting"} for job in jobs.values()))
    parent = os.environ.get("PAPERBRIDGE_DESKTOP_PARENT_PID", "")
    return {"application": "PaperBridge", "workspace": str(ROOT.resolve()),
            "instance_id": os.environ.get("PAPERBRIDGE_DESKTOP_INSTANCE_ID", ""),
            "pid": os.getpid(), "parent_pid": int(parent) if parent.isdecimal() else None,
            "busy": busy}


class CleanupOptions(BaseModel):
    model_config = {"extra": "forbid"}
    memory: bool = True
    temporary: bool = True
    backups: bool = True
    downloads: bool = False
    logs: bool = False


@app.get("/api/storage")
def storage_status(refresh: bool = False):
    return storage_inventory(ROOT, DATA, refresh=refresh)


@app.post("/api/cleanup")
def manual_cleanup(options: CleanupOptions | None = None):
    # Keep the lock through cleanup, so no queued task or export can start
    # using these files or load a model while maintenance is in progress.
    with state_lock, ExitStack() as locks:
        if (active_operations or install_state["status"] == "running"
                or any(j["status"] in {"queued", "running", "exporting"} for j in jobs.values())):
            raise HTTPException(409, "请等待全部翻译、导出及下载任务结束后再清理")
        options = options or CleanupOptions()
        selected = [name for name, value in options.model_dump().items() if value]
        if not selected:
            raise HTTPException(400, "请至少选择一项清理内容")
        try:
            locks.enter_context(download_cache_guard(ROOT, options.downloads))
        except OSError as exc:
            raise HTTPException(409, "模型下载正在进行或下载目录无法安全访问，请取消下载缓存选项后重试") from exc
        before = memory_usage()
        disk_before = disk_usage(ROOT)
        if options.memory:
            release_idle_memory(translator)
        reports = []
        for folder in sorted(DATA.iterdir()) if options.temporary or options.backups else []:
            if not safe_path(DATA, folder) or not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                continue
            try:
                job = get_job(folder.name)
            except (HTTPException, OSError, ValueError):
                continue
            report = cleanup_after_task(folder, job, succeeded=job["status"] == "complete", force=True,
                                        remove_temporary=options.temporary, prune_history=options.backups)
            report.update(model_memory_released=translator.model is None, manual=True, selected=selected)
            job["cleanup_report"] = report
            save_job(job)
            reports.append(report)
        extra = clear_optional_files(ROOT, DATA, selected)
        return {"status": "partial" if extra["errors"] or any(r["errors"] for r in reports) else "complete",
                "selected": selected, "memory_cleaned": options.memory,
                "removed_bytes": sum(r["removed_bytes"] for r in reports) + extra["removed_bytes"],
                "removed_files": sum(r["removed_files"] for r in reports) + extra["removed_files"],
                "old_versions_removed": sum(r["old_versions_removed"] for r in reports),
                "memory_before": before, "memory_after": memory_usage(),
                "disk_before": disk_before, "disk_after": disk_usage(ROOT),
                "model_memory_released": translator.model is None}


@app.get("/api/glossary/{domain}")
def glossary(domain: str):
    if domain not in DOMAINS:
        raise HTTPException(400, "未知学科")
    return terms_for(domain)


@app.get("/api/terminology/{domain}")
def terminology(domain: str):
    if domain not in DOMAINS:
        raise HTTPException(400, "未知学科")
    return {"domain": domain, "entries": terminology_entries(domain), "version": "2026.10.03"}


def install_model():
    try:
        from scripts.download_model import install
        def progress(message):
            install_state["message"] = message
        install(progress)
        install_state.update(status="complete", message="本地模型已就绪")
    except Exception as exc:
        install_state.update(status="failed", message=f"模型下载失败（{type(exc).__name__}），请检查网络后重试")


@app.post("/api/model/install")
def start_install():
    if translator.installed():
        return {"status": "complete"}
    with state_lock:
        if install_state["status"] != "running":
            install_state.update(status="running", message="开始下载约 2.50 GB 的 4B 本地模型" if WINDOWS else "开始下载约 2.3 GB 的本地模型")
            executor.submit(install_model)
    return copy.deepcopy(install_state)


def install_full_resources():
    try:
        from scripts.download_model import install
        if not precision_installed():
            install(lambda text: install_state.update(message=text),
                    "Qwen/Qwen3-14B-GGUF" if WINDOWS else "Qwen/Qwen3-14B-MLX-4bit", PRECISION_DIR)
        if not layout_ready():
            install_state.update(message="正在准备全文排版字体与模型")
            (ROOT / "tmp").mkdir(parents=True, exist_ok=True)
            with (ROOT / "tmp" / "layout-install.log").open("w", encoding="utf-8") as log:
                subprocess.run([sys.executable, str(CODE_ROOT / "scripts/install_layout.py")], cwd=ROOT,
                               stdout=log, stderr=log, check=True, **hidden_process())
        install_state.update(status="complete", message="全文精译资源已就绪，可离线使用")
    except Exception as exc:
        install_state.update(status="failed", message=f"资源下载未完成（{type(exc).__name__}），可重试并继续下载")


@app.post("/api/resources/install")
def start_full_install():
    if layout_ready() and precision_installed():
        return {"status": "complete"}
    with state_lock:
        if install_state["status"] != "running":
            install_state.update(status="running", message="准备全文精译资源，论文不会上传")
            executor.submit(install_full_resources)
    return copy.deepcopy(install_state)


def add_document(raw: bytes, name: str):
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(400, "文件内容不是有效的 PDF")
    job_id = uuid.uuid4().hex
    folder = DATA / job_id
    folder.mkdir()
    source = folder / "original.pdf"
    source.write_bytes(raw)
    try:
        result = analyze(source)
    except Exception as exc:
        source.unlink(missing_ok=True)
        folder.rmdir()
        raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else "PDF 无法读取，请检查文件是否损坏") from exc
    job = {"id": job_id, "name": name, "created_at": time.time(), "status": "ready",
           "message": "论文已解析，可以开始翻译", "progress": 0, "cancel_requested": False,
           "settings": {}, "export_report": None, "has_output": False, **result}
    apply_table_policy(job["segments"])
    with state_lock:
        jobs[job_id] = job
    save_job(job)
    return job_summary(job)


def analyze_uploaded_document(operation, raw, name):
    try:
        return add_document(raw, name)
    finally:
        # Thread cancellation cannot stop PDF analysis. Only the actual worker
        # may release this token once responsibility has been handed to it.
        with state_lock:
            active_operations.discard(operation)


async def finish_upload(operation, raw, name):
    try:
        return await run_in_threadpool(analyze_uploaded_document, operation, raw, name)
    except Exception:
        # Also release the token if thread-pool dispatch fails before execution.
        # CancelledError is deliberately excluded: the worker may still run.
        with state_lock:
            active_operations.discard(operation)
        raise


def upload_finished(task):
    upload_tasks.discard(task)
    if not task.cancelled():
        # A disconnected request no longer awaits this task. Retrieve failures
        # so they do not produce an unhandled-task warning after completion.
        task.exception()


@app.post("/api/documents")
async def upload(request: Request):
    operation = "upload-" + uuid.uuid4().hex
    # Cleanup holds the same lock for its entire operation. Register before
    # receiving bytes, then stay busy until analysis and job.json are saved.
    with state_lock:
        active_operations.add(operation)
    handed_off = False
    try:
        limit = 80 * 1024 * 1024
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > limit:
                raise HTTPException(413, "文件不能超过 80 MB")
        name = Path(unquote(request.headers.get("x-filename", "paper.pdf"))).name[:200]
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "请选择 PDF 文件")
        task = asyncio.create_task(finish_upload(operation, bytes(raw), name))
        upload_tasks.add(task)
        task.add_done_callback(upload_finished)
        handed_off = True
        return await asyncio.shield(task)
    finally:
        if not handed_off:
            with state_lock:
                active_operations.discard(operation)


@app.post("/api/demo")
def demo():
    sample = CODE_ROOT / "examples" / "sci-layout-sample.pdf"
    if not sample.exists():
        raise HTTPException(404, "示例尚未生成，请运行示例生成脚本")
    return add_document(sample.read_bytes(), "合成论文排版测试.pdf")


@app.get("/api/documents")
def documents():
    result = []
    for path in sorted(DATA.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        try:
            job = get_job(path.parent.name)
            if not job.get("diagnostic"):
                result.append(job_summary(job))
        except (ValueError, KeyError):
            continue
    return result


@app.get("/api/documents/{job_id}")
def document(job_id: str):
    return job_summary(get_job(job_id))


@app.get("/api/documents/{job_id}/segments")
def segments(job_id: str, page: int = 1):
    with state_lock:
        job = get_job(job_id)
        return copy.deepcopy(reading_segments(job, [s for s in job["segments"] if s["page"] == page]))


class DocumentTerms(BaseModel):
    glossary: str = Field(default="", max_length=50000)


def document_terminology_result(job):
    saved = job.get("document_terminology", {})
    base = terms_for(job.get("settings", {}).get("domain", "general"), job.get("settings", {}).get("glossary", ""))
    suggestions = [e for e in saved.get("suggestions", saved.get("entries", [])) if e.get("origin") == "model"]
    # Rebuild from source to remove deleted overrides rather than retain them.
    entries = glossary_entries(job["segments"], base, suggestions, job.get("document_term_overrides", ""))
    if saved:
        for e in entries:
            prior = next((p for p in saved.get("entries", []) if p["source"].casefold() == e["source"].casefold()), None)
            if prior and e["origin"] != "user":
                e["pages"] = prior.get("pages", e["pages"])
    return {"job_id": job["id"], "entries": entries, "overrides": job.get("document_term_overrides", ""),
            "generated": bool(saved.get("signature")), "notes": saved.get("notes", [])}


@app.get("/api/documents/{job_id}/terminology")
def document_terminology(job_id: str):
    with state_lock:
        return copy.deepcopy(document_terminology_result(get_job(job_id)))


@app.post("/api/documents/{job_id}/terminology")
def save_document_terminology(job_id: str, settings: DocumentTerms):
    try:
        terms = parse_custom(settings.glossary)
        if len(terms) > 500:
            raise ValueError("本篇自定义术语最多 500 条")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with state_lock:
        job = get_job(job_id)
        if job["status"] in {"running", "queued", "exporting"}:
            raise HTTPException(409, "论文处理中，请完成或停止后再修改本篇术语")
        job["document_term_overrides"] = settings.glossary.strip()
        save_job(job)
        return copy.deepcopy(document_terminology_result(job))


@app.get("/api/documents/{job_id}/issues")
def issues(job_id: str):
    result = []
    with state_lock:
        checked = reading_segments(get_job(job_id))
    for seg in checked:
        if seg["has_issues"]:
            result.append({"id": seg["id"], "page": seg["page"], "status": seg["status"],
                           "source": seg["source"][:200], "reason": seg.get("reason", ""), "notes": seg["notes"],
                           "audit_codes": [item["code"] for item in seg["translation_audit"]]})
    result.sort(key=lambda item: (item["status"] != "failed", item["page"], item["id"]))
    return {"count": len(result), "issues": result,
            "message": "软件自动检查的具体提示，可一键复查修复；提示可能误报，不影响阅读与导出。"}


@app.get("/api/documents/{job_id}/page/{number}")
def preview(job_id: str, number: int, version: str = "original", width: int = 1100):
    get_job(job_id)
    if not 400 <= width <= 3200:
        raise HTTPException(400, "预览宽度须在 400 至 3200 像素之间")
    if version not in {"original", "translated"}:
        raise HTTPException(400, "未知预览类型")
    file = DATA / job_id / f"{version}.pdf"
    if not file.exists():
        raise HTTPException(404, "译文 PDF 尚未生成")
    try:
        return Response(render_page(file, number, width=width), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def annotation_folder(job_id, version):
    job = get_job(job_id)
    if version not in {"original", "translated"}:
        raise HTTPException(400, "未知批注版本")
    folder = DATA / job_id
    if not safe_path(DATA, folder / f"{version}.pdf"):
        raise HTTPException(400, "PDF 文件路径无效")
    if version == "translated":
        if job["status"] in {"queued", "running", "exporting"}:
            raise HTTPException(409, "译文正在更新，请完成后再添加或导出批注")
        if not job.get("has_output"):
            raise HTTPException(404, "译文 PDF 尚未生成")
    return folder


def annotation_result(operation, *args):
    try:
        return operation(*args)
    except AnnotationError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "批注读写失败，请检查磁盘空间后重试") from exc


@contextmanager
def annotation_operation(kind):
    # The native Mac and Windows quit/update probes both read active_operations
    # through /api/desktop/status. Register on the executing worker, and release
    # only after disk work stops even when its HTTP client has disconnected.
    operation = f"annotation-{kind}-{uuid.uuid4().hex}"
    with state_lock:
        active_operations.add(operation)
    try:
        yield
    finally:
        with state_lock:
            active_operations.discard(operation)


@app.get("/api/documents/{job_id}/annotations")
def annotations(job_id: str, version: str = "original"):
    return annotation_result(list_annotations, annotation_folder(job_id, version), version)


@app.post("/api/documents/{job_id}/annotations", status_code=201)
def create_annotation(job_id: str, annotation: AnnotationCreate):
    with annotation_operation("save"):
        return annotation_result(add_annotation, annotation_folder(job_id, annotation.version), annotation)


@app.get("/api/documents/{job_id}/annotations/text")
def annotation_text(job_id: str, page: int = 1, version: str = "original"):
    return annotation_result(page_words, annotation_folder(job_id, version), version, page)


@app.get("/api/documents/{job_id}/annotations/export")
def annotation_export(job_id: str, version: str = "original", document_revision: str | None = None):
    from urllib.parse import quote
    with annotation_operation("export"):
        folder = annotation_folder(job_id, version)
        output = annotation_result(export_annotations, folder, version, document_revision)
        label = "原文" if version == "original" else "中文"
        filename = f"{Path(get_job(job_id)['name']).stem}_{label}_批注.pdf"
        return Response(output, media_type="application/pdf", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"})


@app.delete("/api/documents/{job_id}/annotations/{annotation_id}")
def remove_annotation(job_id: str, annotation_id: str, document_revision: str, version: str = "original"):
    with annotation_operation("delete"):
        return annotation_result(delete_annotation, annotation_folder(job_id, version), version,
                                 annotation_id, document_revision)


class Settings(BaseModel):
    model_config = {"extra": "forbid"}
    domain: str = "general"
    glossary: str = Field(default="", max_length=50000)
    polish: bool = True
    translate_tables: bool = False
    pages: str = Field(default="", max_length=500)
    engine: str = "full"
    profile: str = "precision"
    auto_cleanup: bool = True
    provider: str = "local"
    api_model: str = DEFAULT_MODEL
    api_profile_id: str = Field(default="", max_length=32)
    allow_slow_cpu: bool = False
    force_cpu: bool = False


def local_preflight(settings, *, check_available=False):
    if settings.get("provider", "local") != "local":
        return None
    try:
        return require_local(settings.get("profile", "precision"), settings, windows=WINDOWS, check_available=check_available)
    except LocalRuntimeError as exc:
        raise HTTPException(409, exc.public()) from None


@app.get("/api/local/preflight")
def inspect_local_hardware(profile: str = "precision", force_cpu: bool = False):
    if profile not in {"standard", "precision"}:
        raise HTTPException(400, "未知的本地模型")
    with state_lock:
        occupied = bool(active_operations) or any(j.get("status") in {"queued", "running", "exporting"} for j in jobs.values())
    result = assess_local(profile, windows=WINDOWS, force_cpu=force_cpu, check_available=not occupied)
    result["memory_rechecked_before_loading"] = True
    return result


class StorageSettings(BaseModel):
    auto_cleanup: bool = True


@app.put("/api/documents/{job_id}/storage-settings")
def update_storage_settings(job_id: str, settings: StorageSettings):
    with state_lock:
        job = get_job(job_id)
        if job["status"] in {"running", "queued", "exporting"}:
            raise HTTPException(409, "请在任务结束后更改清理设置")
        job["settings"]["auto_cleanup"] = settings.auto_cleanup
        save_job(job)
        return {"auto_cleanup": settings.auto_cleanup}


def parse_pages(value, count):
    if not value.strip():
        return set(range(1, count + 1))
    result = set()
    for section in value.replace("，", ",").split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", section)
        if not match:
            raise ValueError("页码格式示例：1-3,5；留空表示全部")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= count:
            raise ValueError("指定页码超出论文范围")
        result.update(range(start, end + 1))
    return result


def make_export(job):
    apply_table_policy(job["segments"], job.get("settings", {}).get("translate_tables", False))
    if job["settings"].get("engine") == "full":
        return run_full_translation(job["id"], replay=True)
    folder = DATA / job["id"]
    job["status"], job["message"] = "exporting", "正在生成中文与双语 PDF，检查文字是否放得下"
    save_job(job)
    job["export_report"] = export_pdf(folder / "original.pdf", folder / "translated.pdf", folder / "bilingual.pdf", job["segments"])
    job["has_output"] = True
    save_job(job)


def run_full_translation(job_id, replay=False, repair=False, missing_only=False, run_token=None, api_key=None):
    job = get_job(job_id)
    folder = DATA / job_id
    if not replay:
        with state_lock:
            if run_token and job.get("run_token") != run_token:
                return
            if job.get("cancel_requested"):
                job.update(status="cancelled", message="已停止排队任务，已有译文保留")
                save_job(job)
                return
            # Claim the queue entry before loading resources. It cannot be
            # resubmitted while the worker is starting or stopping.
            job.update(status="running", message="正在准备翻译与排版资源")
            job.pop("local_runtime", None)
            job.pop("failure", None)
            save_job(job)
    with state_lock:
        active_operations.add(job_id)
        if replay and not job.get("last_translation_timing") and (job.get("export_report") or {}).get("elapsed_seconds") is not None:
            job["last_translation_timing"] = {"kind": "translation", "legacy": True,
                                               "elapsed_seconds": job["export_report"]["elapsed_seconds"]}
        started = start_timing(job, "export" if replay else "repair" if repair else "translation")
        save_job(job)
    # Embedded Windows Python can have an isolated _pth file pointing at the
    # installed release. Explicitly select this engine in child processes too.
    command = [sys.executable, "-X", "utf8", "-c",
               "import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('paperbridge.full_worker',run_name='__main__')",
               str(CODE_ROOT), job_id]
    if api_key and not replay:
        command.append("--provider-stdin")
    if replay:
        command.append("--replay")
    if repair:
        command.append("--repair-missing" if missing_only else "--repair")
    process = None
    watchdog = None
    try:
        with (folder / "full-worker.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE if api_key and not replay else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                       text=True, encoding="utf-8", bufsize=1, **hidden_process())
            if not replay and job.get("settings", {}).get("provider", "local") == "local":
                def runtime_state():
                    with state_lock:
                        return dict(job.get("local_runtime") or {})
                watchdog = RuntimeWatchdog(process, runtime_state)
                watchdog.start()
            # Credentials never appear in process arguments, environment, or job
            # files. The child consumes this one line before initializing layout.
            if api_key and not replay:
                process.stdin.write(json.dumps({"api_key": api_key}) + "\n")
                process.stdin.close()
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "state":
                    update = json.loads((folder / "worker-state.json").read_text())
                    with state_lock:
                        cancelled = job.get("cancel_requested", False)
                        timing = job["timing"]
                        last_timing = job.get("last_translation_timing")
                        job.clear()
                        job.update(update)
                        job["timing"] = timing
                        if last_timing:
                            job["last_translation_timing"] = last_timing
                        job["cancel_requested"] = cancelled
                        save_job(job)
            code = process.wait()
            if watchdog:
                watchdog.stop()
            if watchdog and watchdog.failure:
                job.update(status="cancelled" if job.get("cancel_requested") else "failed",
                           message="已停止，已完成译文保留" if job.get("cancel_requested") else watchdog.failure["message"], failure=watchdog.failure)
            if code and job["status"] not in {"failed", "cancelled"}:
                message = "翻译进程意外退出，可能是内存压力或系统中断；已完成译文保留。" + ADVICE
                failure = LocalRuntimeError("model_process_exited", message).public() if job.get("settings", {}).get("provider", "local") == "local" else None
                job.update(status="failed", message=message if failure else "API 处理进程已中断，已完成译文保留", failure=failure)
            # The isolated process has exited, so its model, GPU tensors and
            # native layout sessions have been reclaimed before disk cleanup.
            with state_lock:
                job["cleanup_report"] = cleanup_after_task(folder, job, succeeded=code == 0)
                job["cleanup_report"]["model_memory_released"] = True
                save_job(job)
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        job.update(status="failed", message=f"全文处理未完成：{str(exc)[:180]}")
        save_job(job)
    finally:
        if watchdog:
            watchdog.stop()
        with state_lock:
            finish_timing(job, started)
            active_operations.discard(job_id)
            save_job(job)


def run_translation(job_id, settings, run_token=None, api_key=None):
    job = get_job(job_id)
    with state_lock:
        if run_token and job.get("run_token") != run_token:
            return
        if job.get("cancel_requested"):
            job.update(status="cancelled", message="已停止排队任务，已有译文保留")
            save_job(job)
            return
        job.update(status="running", message="正在准备 API 翻译与本地排版资源" if settings.get("provider", "local") != "local" else "正在准备本地翻译资源")
        save_job(job)
    if settings.get("engine") == "full":
        return run_full_translation(job_id, run_token=run_token, api_key=api_key)
    job = get_job(job_id)
    with state_lock:
        active_operations.add(job_id)
        started = start_timing(job, "translation")
        save_job(job)
    try:
        require_local(settings.get("profile", "standard"), settings, windows=WINDOWS, check_available=True)
        translator.configure_runtime(settings)
        glossary = active_document_terms(job, terms_for(settings["domain"], settings["glossary"]))
        selected = parse_pages(settings["pages"], job["page_count"])
        apply_table_policy(job["segments"], settings.get("translate_tables", False))
        eligible = [s for s in job["segments"] if s["status"] != "preserved" and s["page"] in selected]
        job["status"] = "running"
        title_context = " ".join(s["source"] for s in job["segments"] if s["page"] <= 2 and s["status"] != "preserved")[:2400]
        for index, seg in enumerate(eligible):
            if job["cancel_requested"]:
                break
            job["progress"] = round(100 * index / max(1, len(eligible)))
            job["message"] = f"第 {seg['page']} 页 · 正在翻译第 {index + 1}/{len(eligible)} 个文字区域"
            save_job(job)
            if seg["translation"] and seg["status"] in {"translated", "review", "edited"}:
                continue
            neighbors = [s["source"] for s in job["segments"] if s["page"] == seg["page"] and s["status"] != "preserved"]
            context = title_context + "\n本页上下文：" + " ".join(neighbors)[:3000]
            try:
                seg["translation"], seg["notes"] = translator.translate(seg["source"], context, DOMAINS[settings["domain"]]["name"], glossary, settings["polish"])
                effective = contextual_glossary(seg["source"], glossary, settings["domain"], seg.get("formula_hints"))
                seg["translation_audit"] = audit_translation(seg["source"], seg["translation"], effective)
                seg["status"] = "review" if issue_notes(seg, seg["translation_audit"]) else "translated"
                seg["reason"] = ""
            except TranslationError as exc:
                seg["status"], seg["reason"] = "failed", str(exc)
            save_job(job)
        make_export(job)
        if job["cancel_requested"]:
            job["status"], job["message"] = "cancelled", "已停止；已完成的译文和导出已保存，可继续"
        else:
            reviews = sum(s["has_issues"] for s in reading_segments(job, eligible))
            job["status"] = "complete"
            job["progress"] = 100
            job["message"] = completion_message(reviews)
        save_job(job)
    except Exception as exc:
        if isinstance(exc, LocalRuntimeError):
            job["failure"] = exc.public()
        job["status"] = "failed"
        job["message"] = f"运行失败（{type(exc).__name__}）：{str(exc)[:220]}。已完成段落保存在本机。"
        save_job(job)
    finally:
        translator.unload()
        with state_lock:
            job["cleanup_report"] = cleanup_after_task(DATA / job_id, job, succeeded=job["status"] == "complete")
            job["cleanup_report"]["model_memory_released"] = translator.model is None
            finish_timing(job, started)
            active_operations.discard(job_id)
            save_job(job)


@app.post("/api/documents/{job_id}/translate")
def start_translation(job_id: str, settings: Settings):
    job = get_job(job_id)
    if settings.provider not in {"local", "deepseek", "api"}:
        raise HTTPException(400, "未知的翻译服务")
    if settings.provider != "local" and settings.engine != "full":
        raise HTTPException(400, "API 翻译请使用全文段落模式")
    if settings.provider == "deepseek" and settings.api_model not in MODELS:
        raise HTTPException(400, "未知的 DeepSeek 模型")
    api_key = credentials.get() if settings.provider == "deepseek" else None
    config = None
    if settings.provider == "api":
        config, api_key = profile_action(lambda: api_profiles.resolve(settings.api_profile_id))
    if settings.provider == "deepseek" and not api_key:
        raise HTTPException(409, "请先在翻译服务中填写 DeepSeek API Key")
    if settings.provider == "local" and settings.profile == "standard" and not translator.installed():
        raise HTTPException(409, "请先安装本地模型")
    if settings.domain not in DOMAINS:
        raise HTTPException(400, "未知学科")
    if settings.engine not in {"full", "conservative"} or settings.profile not in {"precision", "standard"}:
        raise HTTPException(400, "未知翻译模式")
    if settings.engine == "conservative" and settings.profile != "standard":
        raise HTTPException(400, "保守替换模式请配合标准模型；精译模型用于全文段落模式")
    if settings.engine == "full" and not layout_ready():
        raise HTTPException(409, "请先准备全文排版资源")
    if settings.provider == "local" and settings.profile == "precision" and not precision_installed():
        raise HTTPException(409, "精译模型正在准备，请下载完成后使用，或选择标准模型")
    assessment = local_preflight(settings.model_dump())
    try:
        terms_for(settings.domain, settings.glossary)
        selected = parse_pages(settings.pages, job["page_count"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if settings.engine == "conservative" and not any(s["status"] != "preserved" and s["page"] in selected for s in job["segments"]):
        raise HTTPException(400, "选定页面没有可安全翻译的文字区域，请查看保留原文的原因")
    with state_lock:
        if job["status"] in {"running", "queued", "exporting"}:
            raise HTTPException(409, "本篇论文正在处理")
        proposed = settings.model_dump()
        if config:
            # Freeze the non-secret configuration for this task. A queued task
            # cannot change destinations when a saved profile is later edited.
            proposed["api_config"] = config
            proposed["api_model"] = config["model"]
        if settings.provider == "local":
            proposed["api_profile_id"] = ""
        changing_engine = job["settings"] and job["settings"].get("engine") != proposed["engine"]
        if changing_engine and any(seg["status"] == "edited" for seg in job["segments"]):
            raise HTTPException(409, "本篇已有手动校订，切换处理方式会改变段落划分。请沿用原方式，或重新导入原稿创建另一份任务")
        omitted_edits = sorted({seg["page"] for seg in job["segments"] if seg["status"] == "edited" and seg["page"] not in selected})
        if omitted_edits:
            raise HTTPException(409, "所选范围未包含已校订的第 " + "、".join(map(str, omitted_edits)) + " 页。请将这些页加入范围，或重新导入原稿另建任务，以保留现有校订")
        if job.get("has_output"):
            job["translation_baseline"] = archive_translation(job)
        def comparable(value):
            normalized = {"provider": "local", "api_model": DEFAULT_MODEL, **value}
            # Provider fields newly introduced in this version must not erase
            # existing local translations when only storage preferences change.
            if normalized["provider"] == "local":
                normalized.pop("api_model", None)
                normalized.pop("api_profile_id", None)
                normalized.pop("api_config", None)
            return {k: v for k, v in normalized.items() if k not in {"auto_cleanup", "allow_slow_cpu", "force_cpu", "translate_tables"}}
        old_translation_settings = comparable(job["settings"])
        new_translation_settings = comparable(proposed)
        if job["settings"] and old_translation_settings != new_translation_settings:
            if changing_engine:
                job.update(analyze(DATA / job_id / "original.pdf"))
                job.pop("reference_boundary", None)
            for seg in job["segments"]:
                if seg["status"] not in {"preserved", "edited"}:
                    seg.update(translation="", status="pending", reason="", notes=[], exported=False)
        apply_table_policy(job["segments"], proposed.get("translate_tables", False))
        job.update(settings=proposed, status="queued", progress=0, message="等待 API 翻译" if settings.provider != "local" else "等待本地模型处理", cancel_requested=False, run_token=uuid.uuid4().hex)
        job.pop("failure", None)
        job.pop("local_runtime", None)
        if assessment:
            job["hardware_assessment"] = assessment
        job["timing"] = {"kind": "translation"}
        job.pop("cleanup_report", None)
        (DATA / job_id / "cancel.request").unlink(missing_ok=True)
        save_job(job)
        if api_key:
            executor.submit(run_translation, job_id, proposed, job["run_token"], api_key=api_key)
        else:
            executor.submit(run_translation, job_id, proposed, job["run_token"])
    return job_summary(job)


def archive_translation(job):
    """Keep rollback files until a new successful export can clean them up."""
    import shutil
    folder = DATA / job["id"]
    history = folder / "history" / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    history.mkdir(parents=True)
    for name in ["job.json", "translated.pdf", "bilingual.pdf", "quality.json", "paragraph-cache.json", "document-terminology.json"]:
        if (folder / name).is_file():
            shutil.copy2(folder / name, history / name)
    return str(history.relative_to(folder))


@app.post("/api/documents/{job_id}/repair")
def repair_translation(job_id: str, missing_only: bool = False, allow_slow_cpu: bool = False, force_cpu: bool = False):
    """Retry incomplete/flagged prose; keep exact unflagged prior translations."""
    import shutil
    job = get_job(job_id)
    repair_settings = {**job.get("settings", {})}
    if allow_slow_cpu: repair_settings["allow_slow_cpu"] = True
    if force_cpu: repair_settings["force_cpu"] = True
    assessment = local_preflight(repair_settings)
    api_key = credentials.get() if job.get("settings", {}).get("provider") == "deepseek" else None
    if job.get("settings", {}).get("provider") == "api":
        saved = job["settings"]
        _, api_key = profile_action(lambda: api_profiles.resolve(saved.get("api_profile_id", ""), saved.get("api_config")))
    if job.get("settings", {}).get("provider") == "deepseek" and not api_key:
        raise HTTPException(409, "请先在翻译服务中填写 DeepSeek API Key，再继续本篇翻译")
    with state_lock:
        if job["status"] in {"running", "queued", "exporting"}:
            raise HTTPException(409, "本篇论文正在处理")
        if job.get("settings", {}).get("engine") != "full" or not any(s.get("translation") for s in job["segments"]):
            raise HTTPException(400, "请先完成一次全文段落翻译")
        folder = DATA / job_id
        job["repair_baseline"] = archive_translation(job)
        job["settings"] = repair_settings
        job.update(status="queued", progress=0, message="等待复核疑点并重试未完成段落", cancel_requested=False, run_token=uuid.uuid4().hex)
        job["timing"] = {"kind": "repair"}
        job.pop("failure", None)
        job.pop("local_runtime", None)
        if assessment: job["hardware_assessment"] = assessment
        job.pop("cleanup_report", None)
        (folder / "cancel.request").unlink(missing_ok=True)
        save_job(job)
        provider_arguments = {"api_key": api_key} if api_key else {}
        if missing_only:
            executor.submit(run_full_translation, job_id, repair=True, missing_only=True, run_token=job["run_token"], **provider_arguments)
        else:
            executor.submit(run_full_translation, job_id, repair=True, run_token=job["run_token"], **provider_arguments)
    return job_summary(job)


@app.post("/api/documents/{job_id}/cancel")
def cancel(job_id: str):
    job = get_job(job_id)
    with state_lock:
        if job["status"] not in {"queued", "running", "exporting"}:
            return {"message": "本篇当前没有正在处理的任务"}
        job["cancel_requested"] = True
        (DATA / job_id / "cancel.request").touch()
        queued = job["status"] == "queued"
        if queued:
            job.update(status="cancelled", message="已停止排队任务，已有译文保留")
        save_job(job)
    return {"message": "排队任务已停止" if queued else "当前段落完成后停止，已完成译文会保存"}


class Edit(BaseModel):
    translation: str = Field(min_length=1, max_length=16000)


class ParagraphEdit(Edit):
    id: str


class BatchEdit(BaseModel):
    edits: list[ParagraphEdit] = Field(min_length=1, max_length=200)


def apply_edits(job_id, edits):
    job = get_job(job_id)
    with state_lock:
        if job["status"] in {"running", "queued", "exporting"}:
            raise HTTPException(409, "请在翻译停止后修改")
        if len({edit.id for edit in edits}) != len(edits):
            raise HTTPException(400, "同一段落不能重复提交")
        selected = []
        for edit in edits:
            seg = next((s for s in job["segments"] if s["id"] == edit.id), None)
            if not seg or seg["status"] == "preserved":
                raise HTTPException(400, "此区域受版面保护，暂不支持替换")
            if job["settings"].get("engine") == "full":
                from .full_worker import validate_edit
                try:
                    validate_edit(seg["source"], edit.translation)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            selected.append((seg, edit.translation.strip()))
        # Validate the entire batch before changing any paragraph.
        for seg, translation in selected:
            seg.update(translation=translation, status="edited", reason="", notes=[])
        job.update(status="exporting", message="修改已保存，正在更新 PDF")
        save_job(job)
    try:
        make_export(job)
        if job["settings"].get("engine") == "full" and job["status"] != "complete":
            raise RuntimeError(job["message"])
        job["status"], job["message"] = "complete", "修改已保存并重新生成 PDF"
        if job["settings"].get("engine") != "full":
            job["cleanup_report"] = cleanup_after_task(DATA / job_id, job, succeeded=True)
        save_job(job)
    except Exception as exc:
        job["status"], job["message"] = "failed", "修改已保存，但 PDF 导出失败，请重试"
        save_job(job)
        raise HTTPException(500, job["message"]) from exc
    return job_summary(job)


@app.put("/api/documents/{job_id}/segments/{segment_id}")
def edit_segment(job_id: str, segment_id: str, edit: Edit):
    return apply_edits(job_id, [ParagraphEdit(id=segment_id, translation=edit.translation)])


@app.put("/api/documents/{job_id}/segments")
def edit_paragraphs(job_id: str, edit: BatchEdit):
    return apply_edits(job_id, edit.edits)


@app.get("/api/documents/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    job = get_job(job_id)
    names = {"original": "original.pdf", "translated": "translated.pdf", "bilingual": "bilingual.pdf", "report": "job.json"}
    if kind not in names:
        raise HTTPException(404, "未知文件")
    path = DATA / job_id / names[kind]
    if not path.exists() or (kind != "original" and kind != "report" and not job["has_output"]):
        raise HTTPException(404, "文件尚未生成")
    label = {"original": "原文", "translated": "中文", "bilingual": "双语对照", "report": "译文与核对记录"}[kind]
    return FileResponse(path, media_type="application/json" if kind == "report" else "application/pdf",
                        filename=f"{Path(job['name']).stem}_{label}{path.suffix}")
