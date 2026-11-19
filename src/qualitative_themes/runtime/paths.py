"""Where the installed app keeps bundled resources and shared, non-project data.

Layout of the bundled resources folder (inside the installed app, read-only):

    resources/
      models/embedding/        sentence-transformers model (config.json, weights, tokenizer)
      engine/ollama[.exe]      standalone Ollama binary (plus its lib/ folder)
      model-packs/*.tmtpack    optional theme-naming model pack(s), or split *.tmtpack.001…

Shared, writable app data (per user, never inside a research project):

    <app data>/
      engine-models/           Ollama model store (OLLAMA_MODELS)
      settings.json            app-level preferences, recent projects
      logs/
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "DocTopicModelTool"
RESOURCES_ENV = "DOC_TOPIC_MODEL_RESOURCES"
APP_DATA_ENV = "DOC_TOPIC_MODEL_APP_DATA"


def resources_dir() -> Path:
    """Read-only resources shipped with the app.

    Order: explicit override (tests, development), the frozen app's bundle
    (PyInstaller sets ``sys._MEIPASS``), then ``<repo>/resources`` for a source
    checkout prepared with ``scripts/fetch_resources.py``.
    """
    override = os.environ.get(RESOURCES_ENV)
    if override:
        return Path(override)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "resources"
    return Path(__file__).resolve().parents[3] / "resources"


def app_data_dir(create: bool = True) -> Path:
    """Per-user writable folder (LOCALAPPDATA / Application Support / XDG data).

    DOC_TOPIC_MODEL_APP_DATA overrides it, which is what the tests use.
    """
    override = os.environ.get(APP_DATA_ENV)
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_DIR_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_DIR_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_embedding_model() -> Path | None:
    """Path to the bundled MiniLM folder, or None if it isn't there."""
    path = resources_dir() / "models" / "embedding"
    return path if (path / "config.json").is_file() else None


def engine_binary() -> Path | None:
    """Path to the bundled ollama binary, or None (source checkout without fetch_resources)."""
    name = "ollama.exe" if sys.platform == "win32" else "ollama"
    path = resources_dir() / "engine" / name
    return path if path.is_file() else None


def engine_models_dir(create: bool = True) -> Path:
    """OLLAMA_MODELS for the bundled engine. Shared by every project."""
    path = app_data_dir(create) / "engine-models"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_model_packs() -> list[Path]:
    folder = resources_dir() / "model-packs"
    if not folder.is_dir():
        return []
    # Single-file packs plus the first part of split packs (name.tmtpack.001).
    return sorted([*folder.glob("*.tmtpack"), *folder.glob("*.tmtpack.001")])


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
