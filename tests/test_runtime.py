"""Installed-app runtime: paths, offline guard, engine lifecycle, model packs, encoder loading."""
import json
import sys
import types
import zipfile

import pytest

from qualitative_themes.analysis import encoder as encoder_module
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.runtime import engine as engine_module
from qualitative_themes.runtime import model_pack, paths
from qualitative_themes.runtime.offline import enable_offline_mode, is_offline


@pytest.fixture
def resources(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    root.mkdir()
    monkeypatch.setenv(paths.RESOURCES_ENV, str(root))
    monkeypatch.setenv(paths.APP_DATA_ENV, str(tmp_path / "appdata"))
    return root


def test_offline_flags_are_set_without_overriding_explicit_values():
    env = {"HF_HUB_OFFLINE": "0"}
    enable_offline_mode(env)
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["HF_HUB_OFFLINE"] == "0"
    assert not is_offline(env)
    assert is_offline(enable_offline_mode({}))


def test_paths_use_overrides_and_detect_bundled_files(resources):
    assert paths.bundled_embedding_model() is None
    model = resources / "models" / "embedding"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    assert paths.bundled_embedding_model() == model
    assert paths.engine_binary() is None
    engine_dir = resources / "engine"
    engine_dir.mkdir()
    (engine_dir / ("ollama.exe" if sys.platform == "win32" else "ollama")).write_text("")
    assert paths.engine_binary() is not None
    assert paths.engine_models_dir().is_dir()


class FakeProcess:
    pid = 4242

    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_engine_starts_private_loopback_copy_and_stops(resources, tmp_path):
    launched = {}
    process = FakeProcess()

    def popen(args, env, **kwargs):
        launched.update(args=args, env=env)
        return process

    calls = {"n": 0}

    def responds(url, timeout=2.0):
        calls["n"] += 1
        return url.endswith(":11999") and calls["n"] > 2

    binary = tmp_path / "ollama"
    binary.write_text("")
    killed = []
    engine = engine_module.LocalEngine(binary=binary, models_dir=tmp_path / "store", port=11999,
                                       popen=popen, responds=responds, processes=lambda binary: [],
                                       kill=killed.append)
    status = engine.start(timeout=5)
    assert status.state == "running" and status.base_url == "http://127.0.0.1:11999"
    assert launched["args"][1] == "serve"
    assert launched["env"]["OLLAMA_HOST"] == "127.0.0.1:11999"
    assert launched["env"]["OLLAMA_MODELS"] == str(tmp_path / "store")
    assert "HTTPS_PROXY" not in launched["env"]
    engine.stop()
    assert killed == [4242] and engine.status.state == "stopped"  # the whole tree, runners included


def test_leftover_engine_processes_are_cleared(tmp_path):
    P = engine_module.EngineProcess
    binary = tmp_path / "ollama"
    binary.write_text("")
    # An earlier session's server (100) with its runner (101), and two runners whose server is gone.
    found = [P(100, 1, f"{binary} serve"), P(101, 100, f"{binary} runner --model a"),
             P(200, 999, f"{binary} runner --model b"), P(300, 998, f"{binary} runner --model c")]
    killed = []

    def engine(responds):
        return engine_module.LocalEngine(binary=binary, models_dir=tmp_path / "store", port=11997,
                                         popen=lambda *a, **k: FakeProcess(), responds=responds,
                                         processes=lambda b: found, kill=killed.append)
    # Earlier server still answering: reuse it, end only the stranded runners.
    reused = engine(lambda url, timeout=2.0: url.endswith(":11997")).start(timeout=1)
    assert reused.state == "running" and killed == [200, 300]
    # Nothing answering: end everything from our folder, then start fresh.
    killed.clear()
    calls = {"n": 0}
    def later(url, timeout=2.0):
        calls["n"] += 1
        return url.endswith(":11997") and calls["n"] > 1
    assert engine(later).start(timeout=2).state == "running"
    assert killed == [100, 200, 300]  # 101 goes with its server's tree


def test_engine_process_listing_never_raises(tmp_path):
    assert engine_module.engine_processes(tmp_path / "no-such-ollama") == []


def test_engine_falls_back_to_system_ollama_or_reports_missing(resources):
    system = engine_module.LocalEngine(responds=lambda url, timeout=2.0: url == engine_module.SYSTEM_URL)
    assert system.start().source == "system"
    missing = engine_module.LocalEngine(responds=lambda url, timeout=2.0: False)
    assert missing.start().state == "missing"


def test_current_base_url_follows_running_engine(resources, monkeypatch):
    monkeypatch.delenv("DOC_TOPIC_MODEL_ENGINE_URL", raising=False)
    engine = engine_module.LocalEngine(port=11998)
    engine.status = engine_module.EngineStatus("running", "http://127.0.0.1:11998", "bundled")
    engine_module.set_engine(engine)
    try:
        assert engine_module.current_base_url() == "http://127.0.0.1:11998"
    finally:
        engine_module.set_engine(None)
    assert engine_module.current_base_url() == engine_module.SYSTEM_URL


def make_pack(path, model="tiny:1b", extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pack.json", json.dumps({"format": model_pack.FORMAT, "model": model}))
        archive.writestr("models/manifests/registry.ollama.ai/library/tiny/1b", "{}")
        archive.writestr("models/blobs/sha256-abc", b"weights")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def test_model_pack_installs_offline_and_ignores_unsafe_paths(resources, tmp_path):
    pack = make_pack(tmp_path / "tiny.tmtpack", extra={"models/../../escape.txt": "x", "other/file": "y"})
    store = tmp_path / "store"
    info = model_pack.install_model_pack(pack, store)
    assert info["model"] == "tiny:1b"
    assert (store / "blobs" / "sha256-abc").read_bytes() == b"weights"
    assert not (tmp_path / "escape.txt").exists()
    assert model_pack.installed_models(store) == ["tiny:1b"]
    # Installing again is a no-op for files already present.
    model_pack.install_model_pack(pack, store)


def test_non_pack_zip_is_rejected(tmp_path):
    bad = tmp_path / "bad.tmtpack"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("pack.json", json.dumps({"format": "other"}))
    with pytest.raises(ValueError):
        model_pack.read_pack_info(bad)


def test_encoder_prefers_bundled_model_and_is_loaded_once(resources, monkeypatch):
    model = resources / "models" / "embedding"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    created = []

    class Encoder:
        def __init__(self, source, **kwargs):
            created.append((source, kwargs))

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = Encoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    encoder_module.clear_encoder_cache()
    first = encoder_module.load_encoder(AnalysisSettings())
    second = encoder_module.load_encoder(AnalysisSettings())
    assert first is second and len(created) == 1
    assert created[0][0] == str(model) and created[0][1]["local_files_only"] is True
    encoder_module.clear_encoder_cache()


def test_missing_encoder_gives_plain_guidance(resources, monkeypatch, tmp_path):
    class Encoder:
        def __init__(self, *args, **kwargs):
            raise OSError("not found")

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = Encoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    encoder_module.clear_encoder_cache()
    with pytest.raises(ValueError, match="Check & repair"):
        encoder_module.load_encoder(AnalysisSettings(), tmp_path)
