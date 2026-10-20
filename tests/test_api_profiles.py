"""Persistent profiles, transport compatibility and immutable task selection."""
import copy
import json
import os

import pytest
from fastapi.testclient import TestClient

import paperbridge.app as app_module
from paperbridge.api_profiles import ProfileManager, normalize_base_url
from paperbridge.providers import APIClient, DeepSeekTranslator, ProviderError
from paperbridge.secret_store import SecretStoreError
from test_deepseek import KEY, Reply, api_job, stream, transport


class Store:
    def __init__(self): self.keys = {}
    def set(self, account, value): self.keys[account] = value
    def get(self, account): return self.keys.get(account, "")
    def delete(self, account): self.keys.pop(account, None)


def config(**changes):
    return {"name": "我的服务", "kind": "custom", "base_url": "https://example.org/v1/",
            "model": "organization/model-id", "api_key": KEY, "json_mode": False, "stream_usage": False, **changes}


def test_save_reload_keep_key_replace_key_and_delete(tmp_path):
    secrets = Store()
    profiles = ProfileManager(tmp_path, secrets)
    row = profiles.save(config())
    assert row["base_url"] == "https://example.org/v1" and row["key_configured"]
    assert KEY not in json.dumps(profiles.status()) and KEY not in profiles.path.read_text()
    if os.name != "nt": assert profiles.path.stat().st_mode & 0o777 == 0o600
    restart = ProfileManager(tmp_path, secrets)
    saved, key = restart.resolve(row["id"])
    assert key == KEY and saved == row
    renamed = restart.save(config(name="备用", api_key=""), row["id"])
    assert renamed["name"] == "备用" and restart.resolve(row["id"])[1] == KEY
    restart.save(config(api_key="sk-replacement-dummy"), row["id"])
    assert restart.resolve(row["id"])[1] == "sk-replacement-dummy"
    restart.delete(row["id"])
    assert not restart.status()["profiles"] and not secrets.keys


def test_changes_do_not_send_saved_credential_to_another_endpoint(tmp_path):
    profiles = ProfileManager(tmp_path, Store())
    row = profiles.save(config())
    with pytest.raises(ValueError, match="地址已改变"):
        profiles.save(config(base_url="https://other.example/v1", api_key=""), row["id"])
    assert profiles.resolve(row["id"])[0] == row
    profiles.save(config(base_url="https://other.example/v1", api_key="sk-other-provider"), row["id"])
    with pytest.raises(ValueError, match="API 地址"):
        profiles.resolve(row["id"], row)


@pytest.mark.parametrize("url", ["http://example.org/v1", "file:///secret", "https://user:password@host/v1",
    "https://host/v1?api_key=dummy", "https://host/v1#key", "https://host/{workspace}/v1", "https://host/../v1",
    "https://host/%2e%2e/v1", "https://host/v1\\a", "https://host:invalid"])
def test_reject_ambiguous_or_insecure_addresses(url):
    with pytest.raises(ValueError): normalize_base_url(url)


def test_full_completion_url_and_local_gateway_normalized():
    assert normalize_base_url(" https://EXAMPLE.org/v1/chat/completions/ ") == "https://example.org/v1"
    assert normalize_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"


def test_native_store_failure_does_not_create_misleading_profile(tmp_path):
    class Locked(Store):
        def set(self, *args): raise SecretStoreError("钥匙串锁定")
    profiles = ProfileManager(tmp_path, Locked())
    with pytest.raises(SecretStoreError): profiles.save(config())
    assert profiles.status()["profiles"] == [] and not profiles.path.exists()


def test_metadata_write_failure_rolls_back_key_change(tmp_path, monkeypatch):
    secrets = Store()
    profiles = ProfileManager(tmp_path, secrets)
    row = profiles.save(config())
    def denied(*args): raise OSError("denied")
    monkeypatch.setattr(profiles, "_write", denied)
    with pytest.raises(OSError): profiles.save(config(api_key="sk-new-dummy-key"), row["id"])
    assert profiles.resolve(row["id"])[1] == KEY


def test_generic_transport_preserves_base_path_and_omits_optional_parameters(monkeypatch):
    calls = transport(monkeypatch, [stream()])
    client = APIClient(KEY, config())
    value = client.generate([{"role": "system", "content": "Return JSON"}], 100)
    assert json.loads(value)["translation"]
    _, url, args = calls[0]
    assert url == "https://example.org/v1/chat/completions"
    assert not {"thinking", "enable_thinking", "response_format", "stream_options"} & args["json"].keys()
    assert client.stats["provider"] == "api" and client.stats["prompt_tokens"] == 100


def test_qwen_preset_disables_thinking_and_accepts_custom_model(monkeypatch):
    calls = transport(monkeypatch, [stream()])
    APIClient(KEY, config(kind="qwen", model="qwen-plus", stream_usage=True)).generate([], 10)
    assert calls[0][2]["json"]["enable_thinking"] is False
    assert calls[0][2]["json"]["stream_options"] == {"include_usage": True}


def test_compatible_service_nonstream_json_response(monkeypatch):
    reply = Reply(body={"choices": [{"message": {"content": '{"translation":"科学观测支持该模型。"}'}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
    reply.headers = {"Content-Type": "application/json; charset=utf-8"}
    transport(monkeypatch, [reply])
    client = APIClient(KEY, config())
    assert json.loads(client.generate([], 100))["translation"] and client.stats["prompt_tokens"] == 20


def test_generic_api_uses_shared_scientific_validation(monkeypatch, tmp_path):
    transport(monkeypatch, [stream()])
    translator = DeepSeekTranslator(KEY, config=config(), failure_dir=tmp_path)
    value, _ = translator.translate("Scientific observations support the model.", "", "科研", {}, False)
    assert value == "科学观测支持该模型。" and translator.inference.stats["provider"] == "api"


@pytest.fixture
def profile_api(api_job, monkeypatch):
    job, calls, folder = api_job
    manager = ProfileManager(folder, Store())
    monkeypatch.setattr(app_module, "api_profiles", manager)
    return job, calls, folder, manager


def test_profile_crud_connection_test_and_validation_never_expose_key(profile_api, monkeypatch):
    _, _, _, manager = profile_api
    transport_calls = transport(monkeypatch, [Reply(body={"data": [{"id": "organization/model-id"}]})])
    with TestClient(app_module.app) as client:
        response = client.post("/api/providers/profiles", json=config())
        assert response.status_code == 200 and KEY not in response.text
        row = response.json()
        for url in ("/api/status", "/api/providers/profiles"):
            assert KEY not in client.get(url).text
        tested = client.post(f"/api/providers/profiles/{row['id']}/test")
        assert tested.status_code == 200 and tested.json()["connected"]
        assert transport_calls[0][0] == "GET" and transport_calls[0][2]["json"] is None
        result = client.put(f"/api/providers/profiles/{row['id']}", json=config(api_key="", name="已改名"))
        assert result.status_code == 200 and manager.resolve(row["id"])[1] == KEY
        invalid = client.post("/api/providers/profiles", json=config(api_key=KEY * 100))
        assert invalid.status_code == 422 and KEY not in invalid.text
        assert client.delete(f"/api/providers/profiles/{row['id']}").status_code == 200
        assert manager.status()["profiles"] == []


def test_queued_translation_has_immutable_config_key_snapshot_and_no_secret_on_disk(profile_api):
    job, calls, folder, manager = profile_api
    row = manager.save(config())
    with TestClient(app_module.app) as client:
        result = client.post(f"/api/documents/{job['id']}/translate", json={"provider": "api", "api_profile_id": row["id"]})
        assert result.status_code == 200
        submitted_settings = copy.deepcopy(calls[0][0][2])
        assert submitted_settings["api_config"]["base_url"] == "https://example.org/v1"
        assert calls[0][1]["api_key"] == KEY
        manager.save(config(base_url="https://another.example/v1", api_key="sk-another-service"), row["id"])
        assert calls[0][0][2] == submitted_settings and calls[0][1]["api_key"] == KEY
        assert KEY not in result.text and KEY not in (folder / job["id"] / "job.json").read_text()
        assert '"api_key"' not in (folder / job["id"] / "job.json").read_text()


def test_missing_or_deleted_profile_does_not_fall_back_to_local_or_other_api(profile_api):
    job, calls, _, manager = profile_api
    row = manager.save(config())
    manager.delete(row["id"])
    with TestClient(app_module.app) as client:
        result = client.post(f"/api/documents/{job['id']}/translate", json={"provider": "api", "api_profile_id": row["id"]})
        assert result.status_code == 409 and "已删除" in result.text
    assert not calls
