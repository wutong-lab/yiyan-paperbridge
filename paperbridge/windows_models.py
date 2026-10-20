"""Pinned Qwen GGUF models and the Windows product's capacity budgets."""
import json
import os
from pathlib import Path
import subprocess
import time

from .paths import CODE_ROOT, ROOT, hidden_process

CONTEXT_TOKENS = 8192
REQUIREMENTS = {
    "standard": {"label": "标准 · 4B", "ram_gb": 16, "recommended_ram_gb": 16,
                 "gpu_vram_gb": 6, "recommended_vram_gb": 8, "disk_gb": 15},
    "precision": {"label": "精译 · 14B", "ram_gb": 24, "recommended_ram_gb": 32,
                  "gpu_vram_gb": 12, "recommended_vram_gb": 16, "disk_gb": 30},
}


def manifest():
    return json.loads((CODE_ROOT / "release-assets/windows-models.json").read_text(encoding="utf-8"))


def model_directory(profile):
    name = "qwen3-14b" if profile == "precision" else "qwen3-4b"
    # Complete editions read immutable weights in place, without a second copy.
    bundle = os.environ.get("PAPERBRIDGE_WINDOWS_BUNDLE")
    if bundle:
        bundled = Path(bundle) / "Models" / name
        if installed(bundled):
            return bundled
    shared = os.environ.get("PAPERBRIDGE_INSTALLED_MODELS_ROOT")
    if shared and installed(Path(shared) / name):
        return Path(shared) / name
    return ROOT / "models" / name


def default_profile():
    bundle = os.environ.get("PAPERBRIDGE_WINDOWS_BUNDLE")
    if bundle:
        try:
            edition = json.loads((Path(bundle) / "BUILD-REPORT.json").read_text(encoding="utf-8")).get("edition")
            if edition in REQUIREMENTS and installed(model_directory(edition)):
                return edition
        except (OSError, ValueError, TypeError):
            pass
    if installed(model_directory("precision")) and not installed(model_directory("standard")):
        return "precision"
    return "standard"


def profile_for(directory):
    return "precision" if Path(directory).name == "qwen3-14b" else "standard"


def installed(directory):
    directory = Path(directory)
    entry = manifest()[profile_for(directory)]
    try:
        info = json.loads((directory / "installation.json").read_text(encoding="utf-8"))
        path = directory / entry["file"]
        with path.open("rb") as handle:
            magic = handle.read(4)
        return (magic == b"GGUF" and path.stat().st_size == entry["bytes"]
                and info.get("revision") == entry["revision"]
                and info.get("sha256") == entry["sha256"])
    except (OSError, ValueError, TypeError):
        return False


_hardware_cache = None
_hardware_time = 0


def hardware(refresh=False):
    global _hardware_cache, _hardware_time
    if not refresh and _hardware_cache is not None and time.monotonic() - _hardware_time < 30:
        return _hardware_cache
    import psutil
    memory = psutil.virtual_memory()
    devices = []
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
                                 "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                timeout=5, check=True, **hidden_process())
        for index, line in enumerate(result.stdout.splitlines()):
            name, total, free, driver = [p.strip() for p in line.split(",")]
            devices.append({"index": index, "name": name, "vram_bytes": int(total) * 1024**2,
                            "free_vram_bytes": int(free) * 1024**2, "driver": driver})
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    _hardware_cache = {"ram_bytes": memory.total, "available_ram_bytes": memory.available,
                       "nvidia_gpus": devices, "context_tokens": CONTEXT_TOKENS,
                       "requirements": REQUIREMENTS, "cpu_supported": True}
    _hardware_time = time.monotonic()
    return _hardware_cache


def engine_choice(profile, info=None):
    info = info or hardware()
    needed = REQUIREMENTS[profile]["gpu_vram_gb"] * 1024**3
    devices = [g for g in info["nvidia_gpus"] if g["free_vram_bytes"] >= needed * 0.95]
    if devices:
        gpu = max(devices, key=lambda g: g["free_vram_bytes"])
        return "cuda", gpu
    return "cpu", None
