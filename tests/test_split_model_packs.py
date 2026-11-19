"""Split (multi-part) theme-naming model packs, needed for llama3.1:8b on GitHub Releases."""
import importlib.util
import json
import os
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from qualitative_themes.analysis import labels
from qualitative_themes.analysis.settings import DEFAULT_NAMING_MODEL
from qualitative_themes.runtime import model_pack, paths

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_model_pack", ROOT / "scripts" / "build_model_pack.py")
build_model_pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_model_pack)

Usage = namedtuple("Usage", "total used free")


def make_big_pack(path, model="llama3.1:8b", blob_size=300_000):
    blob = os.urandom(blob_size)  # incompressible, like real weights
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("pack.json", json.dumps({"format": model_pack.FORMAT, "model": model}))
        archive.writestr("models/manifests/registry.ollama.ai/library/llama3.1/8b", "{}")
        archive.writestr("models/blobs/sha256-big", blob)
        archive.writestr("LICENSE.txt", "license")
    return path, blob


def split(tmp_path, part_size=100_000):
    pack, blob = make_big_pack(tmp_path / "naming-model-llama3.1-8b.tmtpack")
    original = pack.read_bytes()
    parts = build_model_pack.split_in_place(pack, part_size)
    return parts, original, blob


def test_split_in_place_produces_ordered_parts_that_rejoin_exactly(tmp_path):
    parts, original, _ = split(tmp_path)
    assert [p.name for p in parts] == [f"naming-model-llama3.1-8b.tmtpack.{i:03d}" for i in range(1, len(parts) + 1)]
    assert len(parts) == 4
    assert all(p.stat().st_size <= 100_000 for p in parts)
    assert b"".join(p.read_bytes() for p in parts) == original
    assert not (tmp_path / "naming-model-llama3.1-8b.tmtpack").exists()


def test_small_pack_is_not_split(tmp_path):
    pack, _ = make_big_pack(tmp_path / "small.tmtpack", blob_size=10)
    assert build_model_pack.split_in_place(pack, 100_000) == [pack]


def test_split_pack_installs_from_any_part_without_joining(tmp_path):
    parts, _, blob = split(tmp_path)
    store = tmp_path / "store"
    info = model_pack.install_model_pack(parts[0], store)
    assert info["model"] == "llama3.1:8b"
    assert (store / "blobs" / "sha256-big").read_bytes() == blob
    assert not (store.parent / "LICENSE.txt").exists()
    assert model_pack.installed_models(store) == ["llama3.1:8b"]
    # A later part resolves to the same pack.
    assert model_pack.read_pack_info(parts[2])["model"] == "llama3.1:8b"


def test_missing_part_gives_actionable_message(tmp_path):
    parts, _, _ = split(tmp_path)
    parts[-1].unlink()
    with pytest.raises(ValueError, match="incomplete"):
        model_pack.read_pack_info(parts[0])
    parts[0].unlink()
    with pytest.raises(ValueError, match=r"\.001 is missing"):
        model_pack.read_pack_info(parts[1])


def test_corrupted_part_fails_and_leaves_no_partial_file(tmp_path):
    parts, _, _ = split(tmp_path)
    data = bytearray(parts[1].read_bytes())
    data[len(data) // 2] ^= 0xFF
    parts[1].write_bytes(bytes(data))
    store = tmp_path / "store"
    with pytest.raises(Exception):
        model_pack.install_model_pack(parts[0], store)
    assert not list(store.rglob("*.partial"))
    assert not (store / "blobs" / "sha256-big").exists()


def test_insufficient_disk_space_is_reported_before_copying(tmp_path):
    parts, _, _ = split(tmp_path)
    store = tmp_path / "store"
    with pytest.raises(ValueError, match="Not enough disk space"):
        model_pack.install_model_pack(parts[0], store, disk_usage=lambda _p: Usage(10, 10, 1000))
    assert not (store / "blobs").exists()


def test_discovery_lists_first_part_only(tmp_path, monkeypatch):
    parts, _, _ = split(tmp_path)
    single, _ = make_big_pack(tmp_path / "other.tmtpack", blob_size=10)
    assert model_pack.find_packs(tmp_path) == sorted([parts[0], single])
    monkeypatch.setenv(paths.RESOURCES_ENV, str(tmp_path / "res"))
    folder = tmp_path / "res" / "model-packs"
    folder.mkdir(parents=True)
    for part in parts:
        part.rename(folder / part.name)
    assert paths.bundled_model_packs() == [folder / parts[0].name]


def test_defaults_suit_an_8b_naming_model():
    assert DEFAULT_NAMING_MODEL == "llama3.1:8b"
    assert build_model_pack.DEFAULT_MODEL == DEFAULT_NAMING_MODEL
    assert build_model_pack.DEFAULT_PART < build_model_pack.GITHUB_LIMIT
    assert labels.READ_TIMEOUT_SECONDS >= 180
