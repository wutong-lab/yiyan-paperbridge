"""Owned llama.cpp child process; manuscript requests stay on loopback."""
import atexit
import json
import os
import secrets
import socket
import subprocess
import time
import threading
from pathlib import Path

import httpx

from .paths import ROOT, CODE_ROOT, hidden_process
from .windows_models import CONTEXT_TOKENS, engine_choice, hardware, manifest, profile_for
from .local_runtime import (LocalRuntimeError, require_local, assess_local, engine_failure,
                            activity, LOAD_TIMEOUT, GENERATION_TIMEOUT, ADVICE)

LOCAL_PORTS = set()


class GGUFInference:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.process = None
        self.client = None
        self.log = None
        self.port = None
        self.job = None
        self.stream = None
        self.runtime_options = {}
        self.on_activity = None
        self.cancelled = lambda: False
        self.assessment = None
        self.token = secrets.token_urlsafe(32)
        self.alias = "paperbridge-" + secrets.token_hex(12)
        self.stats = {"generation_calls": 0, "prompt_tokens": 0, "generation_seconds": 0.0,
                      "engine": "llama.cpp", "quantization": "Q4_K_M", "context_tokens": CONTEXT_TOKENS,
                      "kv_quantization": False, "device": None}
        atexit.register(self.close)

    def _activity(self, stage, message):
        if self.on_activity:
            self.on_activity(activity(stage, self.stats.get("backend", "cpu"), profile_for(self.directory), message))

    def _failure(self, stage):
        text = ""
        if self.log:
            self.log.flush()
            try:
                with Path(self.log.name).open("rb") as source:
                    source.seek(max(0, source.seek(0, 2) - 65536))
                    text = source.read().decode("utf-8", errors="replace")
            except OSError:
                pass
        return engine_failure(stage, text, self.assessment)

    def _start(self, backend, device):
        self.stats["backend"] = backend
        self._activity("model_loading", f"正在用 {'CPU' if backend == 'cpu' else device['name']} 加载 {'14B' if profile_for(self.directory) == 'precision' else '4B'} 模型，最长等待 4 分钟")
        runtime = Path(os.environ.get("PAPERBRIDGE_LLAMA_DIR", CODE_ROOT / "runtime/llama"))
        executable = runtime / backend / "llama-server.exe"
        if not executable.is_file():
            raise LocalRuntimeError("inference_component_missing", f"缺少 {backend} 推理组件，请重新安装完整的 Windows 应用，或使用已配置的 API。", self.assessment)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        entry = manifest()[profile_for(self.directory)]
        command = [str(executable), "--model", str(self.directory / entry["file"]),
                   "--host", "127.0.0.1", "--port", str(self.port), "--alias", self.alias,
                   "--ctx-size", str(CONTEXT_TOKENS), "--parallel", "1", "--batch-size", "512",
                   "--ubatch-size", "128", "--n-gpu-layers", "99" if backend == "cuda" else "0",
                   "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}',
                   "--cache-type-k", "f16", "--cache-type-v", "f16", "--cache-ram", "0",
                   "--no-context-shift", "--no-webui", "--no-warmup"]
        environment = dict(os.environ, LLAMA_API_KEY=self.token)
        if device is not None:
            environment["CUDA_VISIBLE_DEVICES"] = str(device["index"])
        (ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        self.log = (ROOT / "tmp" / f"inference-{os.getpid()}.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(command, cwd=executable.parent, env=environment,
                                        stdout=self.log, stderr=self.log, **hidden_process())
        if os.name == "nt":
            # The OS kills this model process even if its Python owner crashes.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.job = kernel.CreateJobObjectW(None, None)
            limits = ctypes.create_string_buffer(144)  # x64 EXTENDED_LIMIT_INFORMATION
            ctypes.c_uint32.from_buffer(limits, 16).value = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.job or not kernel.SetInformationJobObject(self.job, 9, limits, len(limits)) or not kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        LOCAL_PORTS.add(self.port)
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}",
                                   headers={"Authorization": f"Bearer {self.token}"},
                                   trust_env=False, timeout=httpx.Timeout(GENERATION_TIMEOUT, connect=5))
        deadline = time.monotonic() + LOAD_TIMEOUT
        while time.monotonic() < deadline:
            if self.cancelled():
                raise LocalRuntimeError("cancelled", "已停止模型加载，已完成译文保留")
            if self.process.poll() is not None:
                raise self._failure("load")
            try:
                response = self.client.get("/v1/models", timeout=3)
                if response.status_code == 200 and any(m.get("id") == self.alias for m in response.json().get("data", [])):
                    self.stats["device"] = device["name"] if device else "CPU"
                    self._activity("ready", f"本地模型已就绪 · {self.stats['device']}")
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.2)
        raise LocalRuntimeError("model_load_timeout", "模型加载超过 4 分钟，已停止本次任务；" + ADVICE, self.assessment)

    def load(self):
        if self.process is not None and self.process.poll() is None:
            return
        options = {**self.runtime_options}
        if os.environ.get("PAPERBRIDGE_CPU_ONLY") == "1": options["force_cpu"] = True
        self.assessment = require_local(profile_for(self.directory), options, windows=True, check_available=True)
        backend, device = self.assessment["backend"], self.assessment["gpu"]
        try:
            self._start(backend, device)
        except Exception as exc:
            failure = exc if isinstance(exc, LocalRuntimeError) else self._failure("load")
            self.close()
            if backend != "cuda" or profile_for(self.directory) == "precision" and not options.get("allow_slow_cpu"):
                raise failure from None
            self.assessment = require_local(profile_for(self.directory), {**options, "force_cpu": True}, windows=True, check_available=True)
            self.stats["gpu_fallback"] = True
            try:
                self._start("cpu", None)
            except Exception as fallback:
                failure = fallback if isinstance(fallback, LocalRuntimeError) else self._failure("load")
                self.close()
                raise failure from None

    def generate(self, messages, max_tokens, response_schema=None):
        self.load()
        started = time.perf_counter()
        self._activity("generating", f"正在使用 {self.stats.get('device') or '本地模型'} 生成译文，单次超过 3 分钟会停止并提示")
        response_format = {"type": "json_object"}
        if response_schema is not None:
            response_format["schema"] = response_schema
        try:
            response = self.client.post("/v1/chat/completions", json={
                "model": self.alias, "messages": messages, "max_tokens": max_tokens,
                "temperature": 0, "seed": 0, "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": response_format, "cache_prompt": True})
            response.raise_for_status()
        except httpx.TimeoutException:
            self.close()
            raise LocalRuntimeError("inference_timeout", "本次模型生成超过 3 分钟，已停止本次任务，已完成段落保留；" + ADVICE, self.assessment) from None
        except httpx.HTTPError:
            failure = self._failure("generate")
            self.close()
            raise failure from None
        finally:
            self._activity("ready", "本次模型请求已结束")
        result = response.json()
        choice = result["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("当前段落达到模型输出长度上限，请重试该段落")
        content = choice["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("本地模型返回了空白译文")
        self.stats["generation_calls"] += 1
        self.stats["prompt_tokens"] += result.get("usage", {}).get("prompt_tokens", 0)
        self.stats["generation_seconds"] += time.perf_counter() - started
        return content

    def clear(self):
        self.close()

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.job is not None:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.job)
            self.job = None
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.log is not None:
            self.log.close()
            self.log = None
        LOCAL_PORTS.discard(self.port)


def block_external_network():
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_pair = socket.socketpair
    pair_context = threading.local()

    def socketpair(*args, **kwargs):
        # CPython implements Windows socketpair through a temporary loopback
        # listener. asyncio needs this private pair to wake its event loop.
        pair_context.creating = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            pair_context.creating = False

    def permitted(address):
        if not isinstance(address, tuple) or len(address) < 2:
            return False
        if getattr(pair_context, "creating", False):
            return address[0] in {"127.0.0.1", "::1"}
        return address[0] == "127.0.0.1" and address[1] in LOCAL_PORTS

    def connect(sock, address):
        if not permitted(address):
            raise OSError("翻译时仅允许连接软件自己的本机推理进程")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not permitted(address):
            raise OSError("翻译时仅允许连接软件自己的本机推理进程")
        return original_connect_ex(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socketpair = socketpair
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
