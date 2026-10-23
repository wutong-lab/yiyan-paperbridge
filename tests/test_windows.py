"""Portable engine contracts; native Windows smoke checks run in CI."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperbridge import windows_models as models
from paperbridge import windows_download as download
from paperbridge.gguf_inference import GGUFInference, LOCAL_PORTS, block_external_network


def test_bundled_models_are_read_in_place_and_select_the_available_edition(tmp_path, monkeypatch):
    import hashlib
    content = b"GGUFoffline-test"
    entry = {"repo": "Qwen/test", "revision": "fixed", "file": "test.gguf", "bytes": len(content),
             "sha256": hashlib.sha256(content).hexdigest()}
    monkeypatch.setattr(models, "manifest", lambda: {"standard": entry, "precision": entry})
    monkeypatch.setattr(models, "ROOT", tmp_path / "personal")
    monkeypatch.setenv("PAPERBRIDGE_WINDOWS_BUNDLE", str(tmp_path / "app"))
    bundled = tmp_path / "app/Models/qwen3-14b"
    bundled.mkdir(parents=True)
    (bundled / entry["file"]).write_bytes(content)
    download.activate(bundled, entry)
    assert models.model_directory("precision") == bundled
    assert models.default_profile() == "precision"
    assert not (tmp_path / "personal").exists()
    standard = tmp_path / "app/Models/qwen3-4b"
    standard.mkdir()
    (standard / entry["file"]).write_bytes(content)
    download.activate(standard, entry)
    (tmp_path / "app/BUILD-REPORT.json").write_text(json.dumps({"edition": "precision"}))
    assert models.default_profile() == "precision"
    (tmp_path / "app/BUILD-REPORT.json").write_text(json.dumps({"edition": "standard"}))
    assert models.default_profile() == "standard"
    # A truncated bundle must never hide a usable personal model directory.
    (bundled / entry["file"]).write_bytes(b"GGUF")
    assert models.model_directory("precision") == tmp_path / "personal/models/qwen3-14b"
    assert models.default_profile() == "standard"


def test_choice_uses_free_vram_and_single_gpu_not_added_capacities():
    gpu = lambda free: {"name": "GPU", "free_vram_bytes": free * 1024**3, "index": 0}
    assert models.engine_choice("precision", {"nvidia_gpus": [gpu(8), gpu(8)]}) == ("cpu", None)
    assert models.engine_choice("standard", {"nvidia_gpus": [gpu(6)]})[0] == "cuda"
    assert models.engine_choice("precision", {"nvidia_gpus": [gpu(16)]})[0] == "cuda"


def test_download_verifies_complete_file_before_marker_and_can_resume(tmp_path, monkeypatch):
    import hashlib
    content = b"GGUF" + bytes(range(64))
    entry = {"repo": "Qwen/test", "revision": "fixed", "file": "test.gguf", "bytes": len(content),
             "sha256": hashlib.sha256(content).hexdigest()}
    monkeypatch.setattr(download, "manifest", lambda: {"standard": entry})
    monkeypatch.setattr(models, "manifest", lambda: {"standard": entry})
    (tmp_path / "test.gguf.download").write_bytes(content[:13])
    class Response:
        status = 206
        headers = {"Content-Range": f"bytes 13-{len(content)-1}/{len(content)}"}
        def __enter__(self): self.remaining = content[13:]; return self
        def __exit__(self, *args): pass
        def read(self, n): value, self.remaining = self.remaining[:n], self.remaining[n:]; return value
    def fetch(request, timeout):
        assert request.headers["Range"] == "bytes=13-"
        assert not (tmp_path / "installation.json").exists()
        return Response()
    monkeypatch.setattr(download.urllib.request, "urlopen", fetch)
    download.install_gguf(model_dir=tmp_path)
    assert (tmp_path / "test.gguf").read_bytes() == content
    assert models.installed(tmp_path)
    (tmp_path / "installation.json").write_text('{}')
    assert not models.installed(tmp_path)


def test_complete_corrupt_download_is_rejected_without_ready_marker(tmp_path, monkeypatch):
    entry = {"repo": "Qwen/test", "revision": "fixed", "file": "test.gguf", "bytes": 4, "sha256": "0"*64}
    monkeypatch.setattr(download, "manifest", lambda: {"standard": entry})
    (tmp_path / "test.gguf.download").write_bytes(b"GGUF")
    with pytest.raises(RuntimeError, match="校验失败"):
        download.install_gguf(model_dir=tmp_path)
    assert not (tmp_path / "installation.json").exists()
    assert not (tmp_path / "test.gguf").exists()


def test_inference_rejects_truncation_and_uses_private_loopback_client(tmp_path):
    engine = GGUFInference(tmp_path)
    engine.load = lambda: None
    requests = []
    def post(path, json):
        requests.append((path, json))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"finish_reason": "length", "message": {"content": '{"translation":"截断"}'}}]})
    engine.client = SimpleNamespace(post=post, close=lambda: None)
    with pytest.raises(RuntimeError, match="长度上限"):
        engine.generate([{"role": "user", "content": "test"}], 100)
    assert requests[0][1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[0][1]["temperature"] == 0
    assert requests[0][1]["response_format"] == {"type": "json_object"}
    engine.close()


def test_translation_schema_is_sent_to_the_owned_local_engine(tmp_path, monkeypatch):
    from paperbridge import translator
    local = translator.LocalTranslator()
    monkeypatch.setattr(translator, "WINDOWS", True)
    engine = GGUFInference(tmp_path)
    engine.load = lambda: None
    requests = []
    def post(path, json):
        requests.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"translation":"注：计数指独立观测。"}'}}]})
    engine.client = SimpleNamespace(post=post, close=lambda: None)
    local.inference = engine
    assert local._generate([{"role": "user", "content": '{"SOURCE":"A scientific note."}'}], 256) == "注：计数指独立观测。"
    schema = requests[0]["response_format"]["schema"]
    assert requests[0]["response_format"]["type"] == "json_object"
    assert schema["required"] == ["translation"]
    assert schema["properties"]["translation"] == {"type": "string", "minLength": 1}
    assert schema["additionalProperties"] is False
    engine.close()


def test_worker_blocks_external_and_unowned_local_connections(monkeypatch):
    import socket
    calls = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, address: calls.append(address))
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: calls.append(address) or 0)
    original = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
    try:
        block_external_network()
        LOCAL_PORTS.add(19283)
        with socket.socket() as sock:
            sock.connect(("127.0.0.1", 19283))
            for address in (("example.com", 443), ("127.0.0.1", 8765), ("localhost", 19283)):
                with pytest.raises(OSError): sock.connect(address)
                with pytest.raises(OSError): sock.connect_ex(address)
        assert calls == [("127.0.0.1", 19283)]
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.socketpair = original
        LOCAL_PORTS.discard(19283)


def test_offline_worker_allows_event_loop_private_wakeup_pair():
    import asyncio
    import socket
    original = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
    try:
        block_external_network()
        left, right = socket.socketpair()
        try:
            left.send(b"wake")
            assert right.recv(4) == b"wake"
        finally:
            left.close(); right.close()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()
        with socket.socket() as sock:
            with pytest.raises(OSError): sock.connect(("127.0.0.1", 8765))
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.socketpair = original


def test_windows_translator_shares_scientific_validation_and_releases_process(monkeypatch, tmp_path):
    import paperbridge.translator as module
    monkeypatch.setattr(module, "WINDOWS", True)
    local = module.LocalTranslator(tmp_path)
    local.inference.load = lambda: None
    local.installed = lambda: True
    local.inference.generate = lambda messages, max_tokens, response_schema=None: '{"translation":"超新星光变曲线"}'
    local.load()
    assert local._generate([{"role":"user", "content":"Supernova light curves"}], 100) == "超新星光变曲线"
    local.unload()
    assert local.model is None and local.inference.process is None


def test_native_update_rechecks_backend_and_unsaved_frontend_before_shutdown():
    """Source contract for the Windows-only host; native execution remains in CI.

    The initial busy probe is insufficient: a translation or note can start while
    the release lookup or confirmation is pending. The second guard must precede
    helper launch and the shutdown that intentionally skips FormClosing.
    """
    source = (Path(__file__).resolve().parents[1] / "desktop/windows/Program.cs").read_text()
    update = source.split("async Task CheckUpdate()", 1)[1]
    confirmation = update.index("DialogResult.OK)return;")
    latest_probe = update.index("using var latestState=JsonDocument.Parse")
    frontend_probe = update.index("bool pendingFrontend=")
    guard = update.index('if(latestState.RootElement.GetProperty("busy").GetBoolean() || pendingFrontend)')
    helper_launch = update.index("Process.Start(start)")
    shutdown = update.index("closing=true;StopServer();Close();")
    assert confirmation < latest_probe < frontend_probe < guard < helper_launch < shutdown
    assert 'http.GetStringAsync(origin+"/api/desktop/status")' in update[latest_probe:frontend_probe]
    assert "desktopBusy()" in update[frontend_probe:guard]
    assert "return;}" in update[guard:helper_launch]
