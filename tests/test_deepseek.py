"""API protocol, confidentiality, offline isolation, and shared job dispatch."""
import io
import json
import socket

import pytest
import requests
from fastapi.testclient import TestClient

import paperbridge.app as app_module
import paperbridge.providers as provider
from paperbridge.translator import TranslationError

KEY = "sk-test-credential-never-persist"


class Reply:
    def __init__(self, status=200, events=None, body=None):
        self.status_code, self.events, self.body = status, events or [], body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): yield json.dumps(self.body).encode()
    def iter_lines(self, **kwargs):
        for event in self.events:
            if isinstance(event, Exception): raise event
            yield b"data: " + json.dumps(event, ensure_ascii=False).encode()
        yield b"data: [DONE]"


def stream(content='{"translation":"科学观测支持该模型。"}', finish="stop"):
    return Reply(events=[{"choices": [{"index": 0, "delta": {"content": content}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 40}},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 40}}])


def transport(monkeypatch, replies):
    calls = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            result = replies.pop(0)
            if isinstance(result, Exception): raise result
            return result
    monkeypatch.setattr(provider.requests, "Session", Session)
    return calls


def test_stream_payload_and_usage_counted_once(monkeypatch):
    calls = transport(monkeypatch, [stream()])
    client = provider.DeepSeekClient(KEY)
    result = client.generate([{"role": "system", "content": "Return JSON"}], 1000)
    assert json.loads(result)["translation"] == "科学观测支持该模型。"
    method, url, args = calls[0]
    assert method == "POST" and url == "https://api.deepseek.com/chat/completions"
    assert args["headers"]["Authorization"] == "Bearer " + KEY
    assert args["allow_redirects"] is False and args["timeout"] == (10, 20)
    assert args["json"]["thinking"] == {"type": "disabled"}
    assert args["json"]["response_format"] == {"type": "json_object"}
    assert client.stats["prompt_tokens"] == 100 and client.stats["completion_tokens"] == 20
    assert KEY not in json.dumps(client.stats)


@pytest.mark.parametrize("code", [301, 400, 401, 402, 403, 404, 429, 500, 503])
def test_errors_have_bounded_retries_and_no_credential_disclosure(monkeypatch, code):
    calls = transport(monkeypatch, [Reply(code), Reply(code)])
    monkeypatch.setattr(provider.time, "sleep", lambda _: None)
    with pytest.raises(provider.ProviderError) as failure:
        provider.DeepSeekClient(KEY).generate([], 100)
    assert len(calls) == (2 if code in {429, 500, 503} else 1)
    assert KEY not in str(failure.value)


def test_incomplete_stream_and_truncation_are_not_successful_translations(monkeypatch):
    transport(monkeypatch, [stream(finish="length"), stream(finish=None)])
    client = provider.DeepSeekClient(KEY)
    with pytest.raises(TranslationError): client.generate([], 100)
    with pytest.raises(provider.ProviderError): client.generate([], 100)


def test_network_timeout_does_not_trigger_repeated_scientific_retries(monkeypatch):
    calls = transport(monkeypatch, [requests.Timeout("secret=" + KEY)])
    translator = provider.DeepSeekTranslator(KEY)
    with pytest.raises(provider.ProviderError) as failure:
        translator.translate("Scientific observations support the model.", "", "科研", {}, False)
    assert len(calls) == 1 and KEY not in str(failure.value)


def test_remote_translator_reuses_scientific_rules_without_native_model(monkeypatch, tmp_path):
    calls = transport(monkeypatch, [stream()])
    translator = provider.DeepSeekTranslator(KEY, failure_dir=tmp_path)
    value, _ = translator.translate("Scientific observations support the model.", "", "科研", {}, False)
    assert value == "科学观测支持该模型。" and len(calls) == 1
    translator.unload()
    assert translator.model is None
    assert not list(tmp_path.iterdir())


def test_cancellation_before_network_and_during_stream(monkeypatch):
    calls = transport(monkeypatch, [stream()])
    client = provider.DeepSeekClient(KEY, cancelled=lambda: True)
    with pytest.raises(provider.ProviderError, match="已停止"): client.generate([], 100)
    assert not calls
    checks = iter([False, True])
    client.cancelled = lambda: next(checks, True)
    with pytest.raises(provider.ProviderError, match="已停止"): client.generate([], 100)
    assert len(calls) == 1


def test_absolute_deadline_even_when_service_keeps_streaming(monkeypatch):
    transport(monkeypatch, [stream()])
    ticks = iter([0, 0, 0, 121, 121])
    monkeypatch.setattr(provider.time, "monotonic", lambda: next(ticks, 121))
    with pytest.raises(provider.ProviderError, match="超过"): provider.DeepSeekClient(KEY).generate([], 100)


def test_connection_test_sends_no_manuscript(monkeypatch):
    calls = transport(monkeypatch, [Reply(body={"data": [{"id": provider.DEFAULT_MODEL}]})])
    assert provider.DeepSeekClient(KEY).test_connection()["connected"]
    assert calls[0][0] == "GET" and calls[0][2]["json"] is None


def test_worker_network_scope_does_not_allow_dependency_downloads(monkeypatch):
    from paperbridge.provider_network import block_worker_network, provider_network
    calls = []
    monkeypatch.setattr(socket.socket, "connect", lambda *a: calls.append(a))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a: calls.append(a))
    monkeypatch.setattr(socket, "create_connection", lambda *a: calls.append(a))
    block_worker_network()
    with pytest.raises(OSError): socket.create_connection(("example.com", 443))
    with provider_network(): socket.create_connection(("api.deepseek.com", 443))
    assert len(calls) == 1
    with pytest.raises(OSError): socket.create_connection(("example.com", 443))


@pytest.fixture
def api_job(tmp_path, monkeypatch):
    doc = {"id": "e" * 32, "name": "test.pdf", "status": "ready", "progress": 0,
           "page_count": 1, "settings": {}, "has_output": False,
           "segments": [{"source": "Science.", "translation": "", "status": "pending", "page": 1}]}
    (tmp_path / doc["id"]).mkdir()
    monkeypatch.setattr(app_module, "DATA", tmp_path)
    monkeypatch.setattr(app_module, "jobs", {doc["id"]: doc})
    monkeypatch.setattr(app_module, "layout_ready", lambda: True)
    monkeypatch.setattr(app_module.translator, "installed", lambda: False)
    monkeypatch.setattr(app_module, "precision_installed", lambda: False)
    monkeypatch.setattr(app_module, "credentials", provider.SessionCredentials())
    calls = []
    class Executor:
        def submit(self, *args, **kw): calls.append((args, kw))
    monkeypatch.setattr(app_module, "executor", Executor())
    return doc, calls, tmp_path


def test_api_job_needs_no_local_model_and_key_never_in_job_or_response(api_job):
    job, calls, folder = api_job
    with TestClient(app_module.app) as client:
        endpoint = f"/api/documents/{job['id']}/translate"
        assert client.post(endpoint, json={"provider": "deepseek"}).status_code == 409
        result = client.put("/api/providers/deepseek/key", json={"api_key": KEY})
        assert result.json()["key_configured"] and KEY not in result.text
        result = client.post(endpoint, json={"provider": "deepseek"})
        assert result.status_code == 200 and KEY not in result.text
        assert calls[0][1]["api_key"] == KEY
        assert KEY not in (folder / job["id"] / "job.json").read_text()
        client.delete("/api/providers/deepseek/key")
        assert not app_module.credentials.get()
        assert calls[0][1]["api_key"] == KEY  # queued task retains its own snapshot
        assert client.post(endpoint, json={"provider": "unknown"}).status_code == 400


def test_validation_does_not_echo_secret_input(api_job):
    with TestClient(app_module.app) as client:
        secret = "sk-" + "sensitive" * 100
        response = client.put("/api/providers/deepseek/key", json={"api_key": secret})
        assert response.status_code == 422 and secret not in response.text


def test_local_mode_still_requires_local_model(api_job):
    job, _, _ = api_job
    with TestClient(app_module.app) as client:
        response = client.post(f"/api/documents/{job['id']}/translate", json={"provider": "local"})
        assert response.status_code == 409
