"""Hardware policy and terminal failures; no model or large allocation required."""
import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

import paperbridge.app as app_module
import paperbridge.local_runtime as runtime
import paperbridge.gguf_inference as gguf
from paperbridge.translator import LocalTranslator
from test_deepseek import api_job


def machine(ram=32, available=24, gpu=0, free=None):
    return {"ram_bytes": ram * runtime.GIB, "available_ram_bytes": available * runtime.GIB,
            "nvidia_gpus": [{"index": 0, "name": "Simulated GPU", "vram_bytes": gpu * runtime.GIB,
                              "free_vram_bytes": (gpu if free is None else free) * runtime.GIB}] if gpu else []}


@pytest.mark.parametrize("ram", [8, 12, 16])
def test_cpu_14b_blocks_low_ram(ram):
    result = runtime.assess_local("precision", windows=True, info=machine(ram, ram - 3))
    assert result["status"] == "blocked" and not result["can_start"]
    assert result["code"] == "insufficient_memory" and "24 GB" in result["message"]
    assert "API" in result["message"]
    assert result["can_use_4b"] is (ram == 16)


def test_no_gpu_is_slow_not_necessarily_impossible():
    result = runtime.assess_local("precision", windows=True, info=machine())
    assert result["status"] == "confirm_cpu" and result["can_start"]
    with pytest.raises(runtime.LocalRuntimeError, match="CPU"):
        runtime.require_local("precision", windows=True, info=machine())
    assert runtime.require_local("precision", {"allow_slow_cpu": True}, windows=True, info=machine())["backend"] == "cpu"
    assert runtime.require_local("standard", windows=True, info=machine(16, 10))["status"] == "ready"


def test_actual_free_vram_and_available_ram_are_checked():
    assert runtime.assess_local("precision", windows=True, info=machine(16, 8, 16))["backend"] == "cuda"
    result = runtime.assess_local("precision", windows=True, info=machine(16, 8, 16, free=4))
    assert result["backend"] == "cpu" and result["status"] == "blocked"
    result = runtime.assess_local("precision", windows=True, info=machine(32, 2), check_available=True)
    assert result["code"] == "insufficient_available_memory"
    # Queued jobs check available memory again when the previous worker exits.
    assert runtime.assess_local("precision", windows=True, info=machine(32, 2), check_available=False)["status"] == "confirm_cpu"


def test_apple_integrated_gpu_is_not_mistaken_for_missing_gpu(monkeypatch):
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "arm64")
    assert runtime.assess_local("precision", windows=False, info=machine(24, 16))["backend"] == "metal"
    assert runtime.assess_local("precision", windows=False, info=machine(24, 16))["status"] == "ready"
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    assert runtime.assess_local("precision", windows=False, info=machine())["code"] == "unsupported_local_hardware"


def test_oom_is_classified_without_exposing_raw_logs():
    error = runtime.engine_failure("generate", "CUDA out of memory. private-debug-token", {"backend": "cuda"})
    assert error.code == "gpu_memory_exhausted" and "显存不足" in str(error)
    assert "private-debug-token" not in json.dumps(error.public())
    assert runtime.engine_failure("load", "MemoryError").code == "insufficient_memory"


def test_gpu_failure_never_silently_falls_back_to_14b_cpu(monkeypatch, tmp_path):
    engine = gguf.GGUFInference(tmp_path / "qwen3-14b")
    assessment = runtime.assess_local("precision", windows=True, info=machine(gpu=16))
    monkeypatch.setattr(gguf, "require_local", lambda *a, **k: assessment)
    starts = []
    def fail(backend, device):
        starts.append(backend)
        raise runtime.LocalRuntimeError("gpu_start_failed", "显卡加速启动失败")
    monkeypatch.setattr(engine, "_start", fail)
    with pytest.raises(runtime.LocalRuntimeError): engine.load()
    assert starts == ["cuda"] and engine.process is None


def test_4b_gpu_failure_can_fall_back_to_cpu_after_capacity_check(monkeypatch, tmp_path):
    engine = gguf.GGUFInference(tmp_path / "qwen3-4b")
    def allowed(profile, options, **kw):
        return runtime.assess_local(profile, windows=True, info=machine(gpu=16), force_cpu=options.get("force_cpu", False))
    monkeypatch.setattr(gguf, "require_local", allowed)
    starts = []
    def start(backend, device):
        starts.append(backend)
        if backend == "cuda": raise RuntimeError("driver unavailable")
    monkeypatch.setattr(engine, "_start", start)
    engine.load()
    assert starts == ["cuda", "cpu"] and engine.stats["gpu_fallback"]


def test_inference_timeout_is_terminal_and_closes_owned_model(monkeypatch, tmp_path):
    engine = gguf.GGUFInference(tmp_path / "qwen3-14b")
    monkeypatch.setattr(engine, "load", lambda: None)
    class Client:
        def post(self,*a,**kw): raise httpx.ReadTimeout("private-request-body")
        def close(self): pass
    engine.client = Client()
    with pytest.raises(runtime.LocalRuntimeError) as failure:
        engine.generate([],100)
    assert failure.value.code == "inference_timeout" and "private-request-body" not in str(failure.value)
    assert engine.client is None


def test_fatal_model_error_is_not_retried_as_bad_translation(monkeypatch, tmp_path):
    import paperbridge.translator as module
    monkeypatch.setattr(module, "WINDOWS", True)
    local = LocalTranslator(tmp_path)
    local.installed = lambda: True
    local.model = local.inference
    calls = []
    def fail(*a):
        calls.append(1)
        raise runtime.LocalRuntimeError("insufficient_memory", "内存不足")
    local.inference.generate = fail
    with pytest.raises(runtime.LocalRuntimeError):
        local.translate("Scientific observations support the model.", "", "科研", {}, True)
    assert calls == [1]


def test_watchdog_terminates_only_its_owned_stalled_worker():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    watchdog = runtime.RuntimeWatchdog(process, lambda: {"stage": "generating", "deadline": time.time() - 1}, interval=.01)
    try:
        watchdog.start()
        process.wait(timeout=3)
        assert watchdog.failure["code"] == "inference_timeout" and process.returncode != 0
    finally:
        watchdog.stop()
        if process.poll() is None: process.kill(); process.wait()


def test_watchdog_does_not_time_out_layout_or_api_phases():
    watchdog = runtime.RuntimeWatchdog(None, lambda: {"stage": "layout", "deadline": 1})
    assert not watchdog.check(now=100)
    watchdog.state = lambda: {"stage": "model_loading", "deadline": 10}
    assert not watchdog.check(now=9)
    assert watchdog.check(now=11) and watchdog.failure["code"] == "model_load_timeout"


def test_low_memory_api_route_does_not_queue_or_change_existing_translations(api_job, monkeypatch):
    job, calls, _ = api_job
    monkeypatch.setattr(app_module, "WINDOWS", True)
    monkeypatch.setattr(app_module, "precision_installed", lambda: True)
    monkeypatch.setattr(runtime, "current_hardware", lambda windows: machine(16, 10))
    with TestClient(app_module.app) as client:
        inspected = client.get("/api/local/preflight?profile=precision").json()
        assert inspected["status"] == "blocked"
        result = client.post(f"/api/documents/{job['id']}/translate", json={"provider":"local","profile":"precision"})
        assert result.status_code == 409 and result.json()["detail"]["code"] == "insufficient_memory"
        assert not calls and job["status"] == "ready"


def test_cpu_14b_requires_explicit_approval_and_records_it(api_job, monkeypatch):
    job, calls, _ = api_job
    monkeypatch.setattr(app_module, "WINDOWS", True)
    monkeypatch.setattr(app_module, "precision_installed", lambda: True)
    monkeypatch.setattr(runtime, "current_hardware", lambda windows: machine())
    with TestClient(app_module.app) as client:
        body = {"provider":"local","profile":"precision"}
        result = client.post(f"/api/documents/{job['id']}/translate", json=body)
        assert result.status_code == 409 and result.json()["detail"]["code"] == "cpu_confirmation_required"
        assert client.post(f"/api/documents/{job['id']}/translate", json={**body,"allow_slow_cpu":True}).status_code == 200
    assert len(calls)==1 and calls[0][0][2]["allow_slow_cpu"] is True


def test_api_provider_bypasses_local_memory_limits(api_job, monkeypatch):
    import paperbridge.providers as providers
    job, calls, _ = api_job
    app_module.credentials.set("sk-synthetic-test-only")
    monkeypatch.setattr(runtime, "current_hardware", lambda windows: machine(8,2))
    with TestClient(app_module.app) as client:
        assert client.post(f"/api/documents/{job['id']}/translate", json={"provider":"deepseek"}).status_code == 200
    assert len(calls)==1
