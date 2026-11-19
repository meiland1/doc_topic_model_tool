"""Make the process offline by construction before any ML library is imported."""
import os

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "TOKENIZERS_PARALLELISM": "false",
}


def enable_offline_mode(environ=None):
    """Set offline flags without overriding values a developer set explicitly."""
    environ = os.environ if environ is None else environ
    for key, value in OFFLINE_ENV.items():
        environ.setdefault(key, value)
    return environ


def is_offline(environ=None):
    """True when the Hugging Face offline flags are set (shown on the start screen)."""
    environ = os.environ if environ is None else environ
    return all(environ.get(key) == value for key, value in OFFLINE_ENV.items()
               if key in {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"})
