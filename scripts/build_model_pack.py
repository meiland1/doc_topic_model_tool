"""Build an offline theme-naming model pack on a connected machine.

    python scripts/build_model_pack.py                          # llama3.1:8b, split for GitHub
    python scripts/build_model_pack.py --model llama3.2:3b
    python scripts/build_model_pack.py --single-file            # one .tmtpack (USB / own hosting)

Needs an ``ollama`` binary: ``resources/engine/ollama`` from fetch_resources.py,
or one on PATH. The pack is a zip of a private Ollama model store plus
``pack.json`` and installs fully offline.

GitHub limits each release file to 2 GiB. Packs larger than ``--part-size``
(default 1.9 GiB) are written as numbered parts ``name.tmtpack.001``, ``.002``…
The app and the Windows installer read the parts directly; keep them together.
Peak disk use is roughly twice the model size (model store + pack).

Check the model's license allows redistribution and pass it with ``--license``.
llama3.1:8b uses the Llama 3.1 Community License, which requires shipping the
license text and showing "Built with Llama".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITHUB_LIMIT = 2 * 1024 ** 3
DEFAULT_PART = int(1.9 * 1024 ** 3)
PORT = 11590
DEFAULT_MODEL = "llama3.1:8b"


def find_ollama() -> str:
    """resources/engine/ollama if fetched, otherwise one on PATH."""
    bundled = ROOT / "resources" / "engine" / ("ollama.exe" if os.name == "nt" else "ollama")
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ollama")
    if not found:
        raise SystemExit("No ollama binary found. Run scripts/fetch_resources.py first.")
    return found


def wait_ready(url: str, timeout: float = 60):
    """Poll the temporary server until it answers, or exit after `timeout` seconds."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url + "/api/tags", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("Ollama did not start.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_in_place(pack: Path, part_size: int) -> list[Path]:
    """Split ``pack`` into numbered parts, working from the end and truncating as it goes.

    This keeps extra disk use to one part instead of a full second copy.
    """
    size = pack.stat().st_size
    count = -(-size // part_size)
    if count <= 1:
        return [pack]
    parts = [pack.with_name(f"{pack.name}.{index:03d}") for index in range(1, count + 1)]
    with pack.open("r+b") as source:
        for index in range(count - 1, 0, -1):
            start = index * part_size
            source.seek(start)
            with parts[index].open("wb") as out:
                remaining = size - start
                while remaining:
                    chunk = source.read(min(remaining, 8 * 1024 * 1024))
                    out.write(chunk)
                    remaining -= len(chunk)
            source.truncate(start)
            size = start
    pack.replace(parts[0])
    return parts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--license", default="", help="license name/URL for the model, stored in pack.json")
    parser.add_argument("--license-file", help="license text to embed in the pack and copy beside it")
    parser.add_argument("--part-size", type=float, default=DEFAULT_PART / 1024 ** 3,
                        help="split packs larger than this many GiB (default 1.9, under GitHub's 2 GiB limit)")
    parser.add_argument("--single-file", action="store_true",
                        help="never split; for USB kits or hosting without a per-file limit")
    args = parser.parse_args(argv)
    part_size = int(args.part_size * 1024 ** 3)
    if not args.single_file and not 0 < part_size < GITHUB_LIMIT:
        raise SystemExit("--part-size must be above 0 and below 2 GiB.")
    binary = find_ollama()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.model.replace(":", "-").replace("/", "-")
    pack = out_dir / f"naming-model-{name}.tmtpack"
    for stale in [pack, *out_dir.glob(f"{pack.name}.[0-9][0-9][0-9]")]:
        stale.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as temporary:
        store = Path(temporary) / "models"
        env = {k: v for k, v in os.environ.items()}
        env.update(OLLAMA_HOST=f"127.0.0.1:{PORT}", OLLAMA_MODELS=str(store))
        server = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_ready(f"http://127.0.0.1:{PORT}")
            subprocess.run([binary, "pull", args.model], env=env, check=True)
        finally:
            server.terminate()
            server.wait(timeout=10)
        size = sum(f.stat().st_size for f in store.rglob("*") if f.is_file())
        info = {"format": "tmt-model-pack-1", "model": args.model, "license": args.license,
                "created": time.strftime("%Y-%m-%d"), "bytes": size}
        # Weights are already compressed; storing avoids slow, pointless deflate.
        with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr("pack.json", json.dumps(info, indent=2))
            if args.license_file:
                archive.write(args.license_file, "LICENSE.txt")
            for file in sorted(store.rglob("*")):
                if file.is_file():
                    archive.write(file, "models/" + file.relative_to(store).as_posix())
                    file.unlink()  # free space as we go
    if args.license_file:
        shutil.copy2(args.license_file, out_dir / f"naming-model-{name}-LICENSE.txt")
    whole = sha256(pack)
    parts = [pack] if args.single_file else split_in_place(pack, part_size)
    print(f"{args.model}: {size / 1024 ** 3:.2f} GiB of model files, pack sha256 {whole}")
    for part in parts:
        print(f"  {part.name}  {part.stat().st_size / 1024 ** 3:.2f} GiB  sha256 {sha256(part)}")
    if len(parts) > 1:
        print("Keep every part together; the app and installer read them in order.")


if __name__ == "__main__":
    sys.exit(main())
