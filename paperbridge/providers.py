"""Optional API transport, sharing the local scientific translation checks.

Workers receive a credential snapshot over stdin; neither job metadata nor
diagnostic files contain the key. Persistent profiles use native secret storage.
"""
import json
import threading
import time

import requests

from .translator import LocalTranslator, TranslationError, parse_model_response
from .provider_network import provider_network
from .api_profiles import validate_config

MODELS = {"deepseek-flash": "DeepSeek Flash", "deepseek-v4-pro": "DeepSeek V4 Pro"}
DEFAULT_MODEL = "deepseek-flash"
API_BASE = "https://api.deepseek.com"


class ProviderError(RuntimeError):
    """Service/credential failures abort the job instead of retrying every paragraph."""


class SessionCredentials:
    def __init__(self):
        self._key = ""
        self._lock = threading.RLock()

    def set(self, value):
        value = value.strip()
        if not 8 <= len(value) <= 512 or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("请填写有效的 DeepSeek API Key")
        try:
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("API Key 应为英文字符，请检查是否粘贴了说明文字") from exc
        with self._lock:
            self._key = value

    def get(self):
        with self._lock:
            return self._key

    def clear(self):
        with self._lock:
            self._key = ""

    def status(self):
        return {"key_configured": bool(self.get()), "key_storage": "session",
                "models": [{"id": k, "name": v} for k, v in MODELS.items()]}


credentials = SessionCredentials()


def service_error(code, label="DeepSeek"):
    return {
        400: "DeepSeek 请求参数未被接受，请检查所选模型或更新软件",
        401: "DeepSeek API Key 无效或已失效，请重新填写",
        402: "DeepSeek 账户余额不足，请充值后继续",
        403: "DeepSeek 拒绝了本次请求，请检查账户权限",
        404: "DeepSeek 模型或接口不可用，请重新选择模型",
        429: "DeepSeek 请求过于频繁，请稍后继续",
        500: "DeepSeek 服务暂时出错，请稍后继续",
        502: "DeepSeek 服务暂时不可达，请稍后继续",
        503: "DeepSeek 服务繁忙，请稍后继续",
    }.get(code, f"DeepSeek 请求未完成（HTTP {code}），请稍后继续").replace("DeepSeek", label)


class APIClient:
    def __init__(self, key, config, cancelled=lambda: False, timeout=120):
        self.config = validate_config(config)
        if not key:
            raise ProviderError("请先在 API 配置中保存密钥")
        self._key, self.model = key, self.config["model"]
        self.base_url, self.label = self.config["base_url"], self.config["name"]
        self.cancelled, self.timeout = cancelled, timeout
        self.on_usage = None
        self.stats = {"provider": "api", "model": self.model, "generation_calls": 0,
                      "http_attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "cached_prompt_tokens": 0, "generation_seconds": 0.0}

    def _check(self, deadline):
        if self.cancelled():
            raise ProviderError("已停止 API 翻译，已完成段落保留")
        if time.monotonic() >= deadline:
            raise ProviderError("API 单次请求超过 120 秒，已停止本次任务；可稍后继续")

    @provider_network()
    def _request(self, path, payload=None, usage_counts=None):
        deadline = time.monotonic() + self.timeout
        for attempt in range(2):
            self._check(deadline)
            self.stats["http_attempts"] += 1
            try:
                with requests.Session() as session:
                    # Keep the user's configured network/proxy settings. Do not
                    # follow redirects carrying a bearer credential elsewhere.
                    with session.request("GET" if payload is None else "POST", self.base_url + path,
                            headers={"Authorization": "Bearer " + self._key,
                                     "Content-Type": "application/json"},
                            json=payload, timeout=(10, 20), stream=True,
                            allow_redirects=False) as response:
                        if response.status_code != 200:
                            if response.status_code in {429, 500, 502, 503} and attempt == 0:
                                for _ in range(10):
                                    self._check(deadline)
                                    time.sleep(0.1)
                                continue
                            raise ProviderError(service_error(response.status_code, self.label))
                        if payload is None or "application/json" in getattr(response, "headers", {}).get("Content-Type", ""):
                            raw = bytearray()
                            for block in response.iter_content(4096):
                                self._check(deadline)
                                raw.extend(block)
                                if len(raw) > 1024 * 1024:
                                    raise ProviderError("API 返回内容过长")
                            result = json.loads(raw)
                            if payload is None:
                                return result
                            self._usage(result.get("usage"), usage_counts)
                            choice = result["choices"][0]
                            return self._complete(choice["message"]["content"], choice.get("finish_reason"))
                        content, finish, size = [], None, 0
                        for line in response.iter_lines(chunk_size=512):
                            self._check(deadline)
                            size += len(line)
                            if size > 8 * 1024 * 1024:
                                raise ProviderError("DeepSeek 返回内容过长，已停止本次请求")
                            if not line.startswith(b"data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == b"[DONE]":
                                break
                            if not raw:
                                continue
                            event = json.loads(raw)
                            if event.get("error"):
                                raise ProviderError("DeepSeek 生成中断，请稍后继续")
                            for choice in event.get("choices", []):
                                if choice.get("index", 0) != 0:
                                    continue
                                value = (choice.get("delta") or {}).get("content")
                                if value:
                                    content.append(value)
                                if choice.get("finish_reason"):
                                    finish = choice["finish_reason"]
                            usage = event.get("usage") or {}
                            for source, target in (("prompt_tokens", "prompt_tokens"),
                                                   ("completion_tokens", "completion_tokens"),
                                                   ("prompt_cache_hit_tokens", "cached_prompt_tokens")):
                                value = usage.get(source)
                                if isinstance(value, int) and value >= 0:
                                    # A service may repeat usage on multiple chunks.
                                    usage_counts[target] = value
                            details = usage.get("prompt_tokens_details") or {}
                            if isinstance(details.get("cached_tokens"), int):
                                usage_counts["cached_prompt_tokens"] = max(0, details["cached_tokens"])
                        return self._complete("".join(content), finish)
            except requests.Timeout as exc:
                raise ProviderError("API 连接或响应超时，已停止本次任务；请检查网络后继续") from None
            except requests.RequestException:
                raise ProviderError("无法连接 API 服务，请检查地址、网络或代理后继续") from None
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                raise ProviderError("API 返回的数据无法读取，请稍后继续") from None

    @staticmethod
    def _usage(usage, counts):
        usage = usage or {}
        for name in ("prompt_tokens", "completion_tokens"):
            if isinstance(usage.get(name), int) and usage[name] >= 0:
                counts[name] = usage[name]

    @staticmethod
    def _complete(content, finish):
        if finish == "length":
            raise TranslationError("API 输出达到长度限制，请求将按完整性规则重试")
        if finish != "stop":
            raise ProviderError("API 未完整返回本段译文，已停止；可稍后继续")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("API 返回空译文，请稍后继续")
        return content

    def test_connection(self):
        result = self._request("/models")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ProviderError("API 模型列表返回异常；如服务商不支持模型列表，可保存配置后尝试翻译")
        available = {row.get("id") for row in result.get("data", []) if isinstance(row, dict)}
        if self.model not in available:
            raise ProviderError("账户连接成功，但模型列表中没有所选模型；请检查完整模型 ID 或服务商的模型权限")
        return {"connected": True, "model": self.model,
                "message": "连接成功，可开始翻译；本次测试仅查询模型列表"}

    def generate(self, messages, max_tokens):
        # Per-client generation is serialized by the scientific translator lock.
        # Accumulate a request's usage once, including truncated responses.
        event_usage = {}
        started = time.monotonic()
        try:
            payload = {
                "model": self.model, "messages": messages, "stream": True,
                "temperature": 0, "max_tokens": max_tokens}
            if self.config["stream_usage"]:
                payload["stream_options"] = {"include_usage": True}
            if self.config["json_mode"]:
                payload["response_format"] = {"type": "json_object"}
            if self.config["kind"] == "deepseek":
                payload["thinking"] = {"type": "disabled"}
            elif self.config["kind"] == "qwen":
                payload["enable_thinking"] = False
            value = self._request("/chat/completions", payload, event_usage)
            return value
        finally:
            self.stats["generation_calls"] += 1
            self.stats["generation_seconds"] += time.monotonic() - started
            for name, count in event_usage.items():
                self.stats[name] += count
            if self.on_usage:
                self.on_usage(dict(self.stats))


class DeepSeekClient(APIClient):
    """Compatibility with 1.10 session-only jobs and API routes."""
    def __init__(self, key, model=DEFAULT_MODEL, cancelled=lambda: False, timeout=120):
        if model not in MODELS:
            raise ValueError("未知的 DeepSeek 模型")
        super().__init__(key, {"name": "DeepSeek", "kind": "deepseek", "base_url": API_BASE,
            "model": model, "json_mode": True, "stream_usage": True}, cancelled, timeout)
        self.stats["provider"] = "deepseek"


class DeepSeekTranslator(LocalTranslator):
    def __init__(self, key, model=DEFAULT_MODEL, failure_dir=None, cancelled=lambda: False, config=None):
        # Avoid initializing native MLX/llama inference in API mode.
        self.failure_dir = failure_dir
        self.model = None
        self.tokenizer = None
        self.lock = threading.RLock()
        self.inference = APIClient(key, config, cancelled) if config else DeepSeekClient(key, model, cancelled)

    def installed(self):
        return True

    def load(self):
        self.model = self.inference

    def unload(self):
        self.model = self.tokenizer = None

    def _generate_json(self, messages, max_tokens, response_schema=None):
        # Provider capabilities remain controlled by the saved API profile;
        # llama.cpp's schema extension is only sent to the owned local engine.
        result = self.inference.generate(messages, max_tokens)
        try:
            value = parse_model_response(result)
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value
        except (ValueError, AttributeError):
            self._record_failure(messages, result, "invalid JSON", max_tokens)
            raise TranslationError("API 未返回有效 JSON，按完整性规则重试") from None
