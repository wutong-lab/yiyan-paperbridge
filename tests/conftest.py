"""Unit tests use a declared machine, never the runner's transient RAM pressure."""
import pytest


@pytest.fixture(autouse=True)
def declared_test_hardware(monkeypatch):
    import paperbridge.local_runtime as runtime
    monkeypatch.setattr(runtime, "current_hardware", lambda windows: {
        "ram_bytes": 32 * runtime.GIB, "available_ram_bytes": 24 * runtime.GIB,
        "nvidia_gpus": [{"index": 0, "name": "Synthetic test GPU", "vram_bytes": 16 * runtime.GIB,
                          "free_vram_bytes": 16 * runtime.GIB}] if windows else []})
