"""Initialize writable Windows resources, then run the owned desktop service."""
import argparse
import os
from pathlib import Path
import shutil
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    from .paths import CODE_ROOT, ROOT
    from .file_lock import lock_file
    ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("tmp", "models", "data/jobs", ".cache"):
        (ROOT / name).mkdir(parents=True, exist_ok=True)
    with (ROOT / ".initialization.lock").open("a+b") as handle:
        lock_file(handle.fileno())
        target = ROOT / ".cache/babeldoc"
        seed = CODE_ROOT / ".cache/babeldoc"
        if not (seed / "ready.json").is_file() and os.environ.get("PAPERBRIDGE_LAYOUT_SEED"):
            seed = Path(os.environ["PAPERBRIDGE_LAYOUT_SEED"])
        if not (seed / "ready.json").is_file():
            existing = Path(os.environ.get("LOCALAPPDATA", ROOT.parent)) / "PaperBridge/.cache/babeldoc"
            if (existing / "ready.json").is_file():
                seed = existing
        if not (target / "ready.json").is_file():
            if not (seed / "ready.json").is_file():
                raise RuntimeError("未找到本地排版资源，请先完成正式版安装后再打开预览版")
            shutil.copytree(seed, target, dirs_exist_ok=True)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from .desktop_server import main as serve
    return serve(["--parent-pid", str(args.parent_pid), "--state-file", str(args.state_file)])


if __name__ == "__main__":
    result = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(result)
