"""Saved API profiles. Public metadata and native credentials stay separate."""
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit

from .secret_store import SecretStoreError, atomic_private_write, native_store

PRESETS = [
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
     "models": ["deepseek-flash", "deepseek-v4-pro"], "json_mode": True, "stream_usage": True,
     "hint": "使用 DeepSeek 开放平台的 API Key"},
    {"id": "qwen", "name": "通义千问 · 阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus", "models": ["qwen-plus", "qwen-turbo"], "json_mode": False, "stream_usage": True,
     "hint": "填写与 API Key 同地域的 Base URL，也可使用业务空间专属域名"},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "model": "", "models": [], "json_mode": False, "stream_usage": True,
     "hint": "从服务商控制台复制完整的模型 ID（包括组织前缀）"},
    {"id": "custom", "name": "自定义 · OpenAI 兼容接口", "base_url": "", "model": "", "models": [],
     "json_mode": False, "stream_usage": False,
     "hint": "支持 Chat Completions 格式；填写 Base URL 和模型 ID"},
]


def clean_key(value):
    value = value.strip()
    if not 8 <= len(value) <= 512 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("请填写有效的 API Key（英文字符，不含空格）")
    return value


def normalize_base_url(value):
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("API 地址格式不正确") from None
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("请填写 API Base URL，地址中不要包含密钥、查询参数或登录信息")
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("API 地址需要使用 HTTPS；本机 localhost 接口可以使用 HTTP")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "{" in value or "}" in value:
        raise ValueError("请填写完整的 API 地址，替换地址中的占位符")
    path = parts.path.rstrip("/")
    # Accept the full completion URL often copied from a provider's console.
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    if any(item in {".", ".."} for item in path.split("/")) or "%" in path or "\\" in value:
        raise ValueError("API 地址包含不支持的路径")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


def validate_config(value):
    kind = value.get("kind", "custom")
    if kind not in {p["id"] for p in PRESETS}:
        raise ValueError("未知的 API 服务类型")
    name = str(value.get("name", "")).strip()
    model = str(value.get("model", "")).strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("请输入配置名称，最多 80 个字符")
    if not model or len(model) > 200 or any(ord(c) <= 32 or ord(c) >= 127 for c in model):
        raise ValueError("请填写服务商提供的完整模型 ID，不含空格")
    base = normalize_base_url(str(value.get("base_url", "")))
    return {"name": name, "kind": kind, "base_url": base, "model": model,
            "json_mode": bool(value.get("json_mode", False)), "stream_usage": bool(value.get("stream_usage", False))}


class ProfileManager:
    def __init__(self, root, store=None):
        self.folder = Path(root) / "settings"
        self.path = self.folder / "api-profiles.json"
        self.namespace = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:20]
        self._store = store
        self._lock = threading.RLock()

    @property
    def store(self):
        if self._store is None:
            self._store = native_store(self.folder, self.namespace)
        return self._store

    def _read(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("profiles"), list):
                raise ValueError()
            return data["profiles"]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError, AttributeError):
            raise ValueError("API 配置文件无法读取，请保留文件并联系维护者") from None

    def _write(self, rows):
        atomic_private_write(self.path, json.dumps({"version": 1, "profiles": rows}, ensure_ascii=False, indent=2).encode("utf-8"))

    @staticmethod
    def _id(value):
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("未找到该 API 配置")
        return value

    def status(self):
        with self._lock:
            rows = self._read()
            # No credential read (or Keychain prompt) during background polling.
            return {"profiles": rows, "presets": PRESETS, "persistent": True,
                    "key_storage": "macOS 钥匙串 / Windows 用户加密存储"}

    def save(self, value, profile_id=None):
        config = validate_config(value)
        key = clean_key(value["api_key"]) if value.get("api_key", "").strip() else ""
        with self._lock:
            rows = self._read()
            old = next((p for p in rows if p["id"] == profile_id), None)
            if profile_id and old is None:
                raise ValueError("未找到该 API 配置，请重新选择")
            if not old and len(rows) >= 50:
                raise ValueError("最多保存 50 套 API 配置")
            if not key and (not old or not old.get("key_configured")):
                raise ValueError("首次保存请填写 API Key")
            if old and old["base_url"] != config["base_url"] and not key:
                raise ValueError("API 地址已改变，请重新填写该地址对应的 API Key")
            profile_id = profile_id or uuid.uuid4().hex
            prior = self.store.get(profile_id) if key and old else ""
            if key:
                self.store.set(profile_id, key)
            row = {"id": profile_id, **config, "key_configured": True}
            updated = [row if p["id"] == profile_id else p for p in rows] if old else [*rows, row]
            try:
                self._write(updated)
            except BaseException:
                if key:
                    if prior: self.store.set(profile_id, prior)
                    else: self.store.delete(profile_id)
                raise
            return row

    def resolve(self, profile_id, snapshot=None):
        with self._lock:
            self._id(profile_id)
            row = next((p for p in self._read() if p["id"] == profile_id), None)
            if not row:
                raise ValueError("这篇论文使用的 API 配置已删除，请选择可用配置后继续翻译")
            if snapshot and row["base_url"] != snapshot.get("base_url"):
                raise ValueError("这篇论文的 API 地址与当前配置不同，请重新选择翻译服务后继续")
            key = self.store.get(profile_id)
            if not key:
                raise SecretStoreError("已保存的 API 密钥不可用，请在 API 配置中重新填写并保存")
            return dict(snapshot or row), key

    def delete(self, profile_id):
        with self._lock:
            self._id(profile_id)
            rows = self._read()
            row = next((p for p in rows if p["id"] == profile_id), None)
            if not row:
                raise ValueError("未找到该 API 配置")
            # Keep metadata if the OS denies credential deletion.
            self.store.delete(profile_id)
            self._write([p for p in rows if p["id"] != profile_id])
