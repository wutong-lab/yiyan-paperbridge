"""Conservative product budgets and terminal local-inference failures.

These are supported configuration budgets for our pinned 4-bit models and
8192-token context, not universal limits for every 14B implementation.
"""
import platform
import threading
import time

import psutil

from .paths import WINDOWS

GIB = 1024 ** 3
LOAD_TIMEOUT = 240
GENERATION_TIMEOUT = 180
ADVICE = "可切换本地 4B，或选择已配置的 API。"


class LocalRuntimeError(RuntimeError):
    """Fatal engine errors must never be retried paragraph by paragraph."""
    def __init__(self, code, message, assessment=None):
        super().__init__(message)
        self.code, self.assessment = code, assessment

    def public(self):
        return {"code": self.code, "message": str(self), "assessment": self.assessment,
                "kind": "local_runtime", "actions": ["standard", "api"]}


def current_hardware(windows):
    memory = psutil.virtual_memory()
    if windows:
        from .windows_models import hardware
        return {**hardware(refresh=True), "ram_bytes": memory.total, "available_ram_bytes": memory.available}
    return {"ram_bytes": memory.total, "available_ram_bytes": memory.available, "nvidia_gpus": []}


def assess_local(profile="precision", *, windows=None, info=None, force_cpu=False, check_available=False):
    if profile not in {"standard", "precision"}:
        raise ValueError("未知的本地模型")
    windows = WINDOWS if windows is None else windows
    if info is None:
        info = current_hardware(windows)
    precision = profile == "precision"
    label = "14B" if precision else "4B"
    backend, gpu = "metal", None
    if windows:
        from .windows_models import engine_choice
        backend, gpu = ("cpu", None) if force_cpu else engine_choice(profile, info)
    standard_available = assess_local("standard", windows=windows, info=info, force_cpu=force_cpu,
                                     check_available=check_available)["can_start"] if precision else False
    advice = ADVICE if standard_available else "可选择已配置的 API 翻译。"
    minimum = (16 if precision else 8) if backend == "cuda" else (24 if precision else 16)
    available_min = (6 if precision else 3) if backend == "cuda" else (12 if precision else 5) if windows else (10 if precision else 4)
    total, available = info["ram_bytes"] / GIB, info["available_ram_bytes"] / GIB
    result = {"profile": profile, "label": label, "backend": backend, "gpu": gpu,
              "ram_gb": round(total, 1), "available_ram_gb": round(available, 1),
              "minimum_ram_gb": minimum, "minimum_available_ram_gb": available_min,
              "can_use_4b": standard_available,
              "status": "ready", "can_start": True, "message": "",
              "load_timeout_seconds": LOAD_TIMEOUT, "generation_timeout_seconds": GENERATION_TIMEOUT}
    if not windows and platform.system() == "Darwin" and platform.machine() != "arm64":
        result.update(status="blocked", can_start=False, code="unsupported_local_hardware",
                      message="本地模型需要 Apple Silicon Mac；这台 Mac 请使用已配置的 API 翻译。")
    elif total < minimum * .95:
        result.update(status="blocked", can_start=False, code="insufficient_memory",
                      message=f"当前电脑内存约 {total:.1f} GB，本软件的 {label} {'CPU ' if backend == 'cpu' else ''}模式要求至少 {minimum} GB 内存。{advice}")
    elif check_available and available < available_min * .95:
        result.update(status="blocked", can_start=False, code="insufficient_available_memory",
                      message=f"当前可用内存约 {available:.1f} GB，{label} 启动预留至少 {available_min} GB。请先关闭占内存的程序，再重试；{advice}")
    elif backend == "cpu" and precision:
        reason = "已选择仅使用 CPU" if force_cpu else "当前可用显存不足以启用 14B 加速" if info.get("nvidia_gpus") else "未检测到可用的 NVIDIA 加速"
        result.update(status="confirm_cpu", code="cpu_confirmation_required",
                      message=f"{reason}，14B 将使用 CPU。当前内存约 {total:.1f} GB；此模式可能很慢，请确认后继续，或切换 4B / API。")
    else:
        device = gpu["name"] if gpu else "Apple 集成 GPU" if backend == "metal" else "CPU"
        result["message"] = f"{label} 将使用 {device} 翻译。"
    return result


def require_local(profile, options=None, **kwargs):
    options = options or {}
    assessment = assess_local(profile, force_cpu=options.get("force_cpu", False), **kwargs)
    if assessment["status"] == "blocked" or (assessment["status"] == "confirm_cpu" and not options.get("allow_slow_cpu")):
        raise LocalRuntimeError(assessment.get("code", "hardware_unavailable"), assessment["message"], assessment)
    return assessment


def engine_failure(stage, detail="", assessment=None):
    text = str(detail).lower()
    if any(marker in text for marker in ("out of memory", "bad_alloc", "cannot allocate memory", "failed to allocate", "not enough memory", "insufficient memory", "memoryerror", "allocation failed", "cuda_error_out_of_memory")):
        gpu = "cuda" in text or (assessment or {}).get("backend") == "cuda"
        return LocalRuntimeError("gpu_memory_exhausted" if gpu else "insufficient_memory",
            ("显存不足，模型无法继续运行。请关闭占用显存的软件后重试；" if gpu else "运行内存不足，模型无法继续运行。请关闭占内存的软件后重试；") + ADVICE, assessment)
    return LocalRuntimeError("gpu_start_failed" if stage == "load" and (assessment or {}).get("backend") == "cuda" else "model_load_failed" if stage == "load" else "model_process_exited",
        ("显卡加速未能启动，请检查 NVIDIA 驱动。软件没有自动改用 14B CPU 慢速翻译；" if stage == "load" and (assessment or {}).get("backend") == "cuda" else "本地模型加载失败；" if stage == "load" else "本地推理进程已中断，已完成译文保留；") + ADVICE, assessment)


def activity(stage, backend, profile, message):
    limit = LOAD_TIMEOUT if stage == "model_loading" else GENERATION_TIMEOUT if stage == "generating" else None
    now = time.time()
    return {"stage": stage, "backend": backend, "profile": profile, "message": message,
            "started_at": now, "deadline": now + limit if limit else None}


class RuntimeWatchdog:
    """Stop only the owned worker if an explicitly timed model phase stalls."""
    def __init__(self, process, state, interval=.5):
        self.process, self.state, self.interval = process, state, interval
        self.failure = None
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, name="local-model-watchdog", daemon=True)

    def start(self):
        self.thread.start()

    def check(self, now=None):
        phase = self.state() or {}
        deadline = phase.get("deadline")
        if phase.get("stage") not in {"model_loading", "generating"} or not isinstance(deadline, (int, float)):
            return False
        if (time.time() if now is None else now) <= deadline:
            return False
        loading = phase["stage"] == "model_loading"
        self.failure = LocalRuntimeError("model_load_timeout" if loading else "inference_timeout",
            ("模型加载超过 4 分钟，已停止本次任务；" if loading else "本次模型生成超过 3 分钟，已停止本次任务，已完成段落保留；") + ADVICE).public()
        return True

    def _run(self):
        while not self.finished.wait(self.interval):
            if self.check():
                try:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                except Exception:
                    try: self.process.kill()
                    except OSError: pass
                return

    def stop(self):
        self.finished.set()
        self.thread.join(timeout=4)
