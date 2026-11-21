"""Fetch the files the installer bundles. Run on a connected build machine (or CI).

    python scripts/fetch_resources.py              # embedding model + engine for this OS
    python scripts/fetch_resources.py --skip-engine
    python scripts/fetch_resources.py --engine-gpu all     # keep every GPU library (large)

Produces ``resources/`` at the repository root:

    resources/models/embedding/   all-MiniLM-L6-v2 at the revision pinned in settings.py
    resources/engine/             standalone Ollama binary (+ libraries) for this OS
    resources/LICENSES/           license texts shipped with the app

Engine GPU libraries (Windows/Linux): the Ollama download carries CUDA 12, CUDA 13
and Vulkan libraries. The Windows zip is ~1.9 GB, mostly ``lib/ollama/cuda_v12``
(~2.3 GB unpacked), which would push the installer over GitHub's 2 GiB file limit.
``--engine-gpu`` chooses what to keep:

    standard (default)  drop cuda_v12; keep cuda_v13 + vulkan (+ CPU)
    all                 keep everything
    cpu                 CPU only (smallest; naming with an 8B model will be slow)

Machines whose NVIDIA driver is too old for CUDA 13 fall back to Vulkan or CPU.

The pinned Ollama version is ``OLLAMA_VERSION`` below (override with the
``OLLAMA_VERSION`` environment variable). Check asset names on
https://github.com/ollama/ollama/releases when you change it.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
OLLAMA_VERSION = os.environ.get("OLLAMA_VERSION", "0.12.11")
RELEASES = f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}"


def pinned_model():
    """(repo, revision) of the embedding model, read from AnalysisSettings so there's one source of truth."""
    sys.path.insert(0, str(ROOT / "src"))
    from qualitative_themes.analysis.settings import AnalysisSettings
    settings = AnalysisSettings()
    return settings.embedding_model, settings.embedding_revision


def fetch_embedding_model():
    """Replace resources/models/embedding with the pinned revision and write its license note.

    Needs network access (Hugging Face). Exits if the download has no config.json.
    """
    from huggingface_hub import snapshot_download
    repo, revision = pinned_model()
    target = RESOURCES / "models" / "embedding"
    if target.exists():
        shutil.rmtree(target)
    print(f"Downloading {repo}@{revision[:12]} …")
    snapshot_download(repo_id=repo, revision=revision, local_dir=target,
                      allow_patterns=["*.json", "*.txt", "*.safetensors", "1_Pooling/*", "modules.json",
                                      "sentence_bert_config.json", "tokenizer*", "vocab.txt", "README.md"])
    if not (target / "config.json").is_file():
        raise SystemExit("Embedding model download looks incomplete (no config.json).")
    license_dir = RESOURCES / "LICENSES"
    license_dir.mkdir(parents=True, exist_ok=True)
    (license_dir / "all-MiniLM-L6-v2.txt").write_text(
        f"{repo} (revision {revision}) is distributed under the Apache License 2.0.\n"
        "See https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2\n")


def download(url: str) -> bytes:
    """Fetch a URL into memory, showing progress, then print its size and SHA-256.

    The SHA-256 is worth recording when bumping the pin. Progress goes on one
    line (carriage return), every 1% or so, so a 2 GB download doesn't look stuck.
    """
    print(f"Downloading {url} …")
    chunks, received, shown = [], 0, -1
    with urllib.request.urlopen(url) as response:
        total = int(response.headers.get("Content-Length") or 0)
        while chunk := response.read(1 << 20):
            chunks.append(chunk)
            received += len(chunk)
            step = int(received * 100 / total) if total else received // (50 << 20)
            if step != shown:
                shown = step
                done = f"{received / 1e6:,.0f} of {total / 1e6:,.0f} MB ({step}%)" if total else f"{received / 1e6:,.0f} MB"
                print(f"\r  {done}", end="", flush=True)
    print()
    data = b"".join(chunks)
    print(f"  {len(data) / 1e6:.1f} MB · sha256 {hashlib.sha256(data).hexdigest()}")
    return data


GPU_DROP = {"standard": ("cuda_v12",), "all": (), "cpu": ("cuda_v12", "cuda_v13", "vulkan", "rocm")}


def prune_gpu_libraries(target: Path, variant: str):
    """Delete the GPU library folders that `variant` drops (see GPU_DROP). No-op on macOS."""
    library = next((p for p in [target / "lib" / "ollama"] if p.is_dir()), None)
    if library is None:
        return
    for name in GPU_DROP[variant]:
        for folder in library.glob(f"{name}*"):
            if folder.is_dir():
                size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                shutil.rmtree(folder)
                print(f"  removed lib/ollama/{folder.name} ({size / 1e6:.0f} MB) for --engine-gpu {variant}")


def fetch_engine(gpu: str = "standard"):
    """Replace resources/engine with the pinned Ollama build for this OS, then prune GPU libraries.

    Post: resources/engine/ollama[.exe] exists with its lib/ folder next to it,
    plus resources/LICENSES/ollama.txt.
    """
    target = RESOURCES / "engine"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    system = platform.system()
    if system == "Windows":
        data = download(f"{RELEASES}/ollama-windows-amd64.zip")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(target)
    elif system == "Darwin":
        # The standalone CLI tarball contains the ollama binary and its libraries.
        data = download(f"{RELEASES}/ollama-darwin.tgz")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            archive.extractall(target, filter="data")
        binary = target / "ollama"
        if not binary.is_file():
            # rglob also matches folders (lib/ollama), so only accept a file.
            nested = next((p for p in target.rglob("ollama") if p.is_file()), None)
            if nested is None:
                raise SystemExit("Could not find the ollama binary in the macOS download.")
            shutil.move(str(nested), binary)
        binary.chmod(0o755)
    else:
        data = download(f"{RELEASES}/ollama-linux-amd64.tgz")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            archive.extractall(target, filter="data")
        # The tarball has bin/ollama (the program) and lib/ollama/ (its libraries).
        # Copy the program next to lib/ so it finds lib/ollama the same way on every OS.
        found = target / "bin" / "ollama"
        if not found.is_file():
            found = next((p for p in target.rglob("ollama") if p.is_file()), None)
        if found is None:
            raise SystemExit("Could not find the ollama binary in the Linux download.")
        if found != target / "ollama":
            shutil.copy2(found, target / "ollama")
        (target / "ollama").chmod(0o755)
    prune_gpu_libraries(target, gpu)
    license_dir = RESOURCES / "LICENSES"
    license_dir.mkdir(parents=True, exist_ok=True)
    (license_dir / "ollama.txt").write_text(
        f"Ollama {OLLAMA_VERSION} is distributed under the MIT License.\n"
        "Copyright (c) Ollama. See https://github.com/ollama/ollama/blob/main/LICENSE\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-model", action="store_true")
    parser.add_argument("--skip-engine", action="store_true")
    parser.add_argument("--engine-gpu", choices=sorted(GPU_DROP), default="standard",
                        help="which engine GPU libraries to bundle (default: standard)")
    args = parser.parse_args()
    RESOURCES.mkdir(exist_ok=True)
    if not args.skip_model:
        fetch_embedding_model()
    if not args.skip_engine:
        fetch_engine(args.engine_gpu)
    print(f"Resources ready in {RESOURCES}")


if __name__ == "__main__":
    main()
