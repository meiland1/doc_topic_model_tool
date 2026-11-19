"""Setup health check for the start screen's Local setup panel and ``--self-test``."""
from __future__ import annotations

from dataclasses import dataclass

from qualitative_themes.runtime import model_pack
from qualitative_themes.runtime.engine import get_engine
from qualitative_themes.runtime.offline import is_offline
from qualitative_themes.runtime.paths import bundled_embedding_model, engine_binary


@dataclass(frozen=True)
class Check:
    """One row of the Local setup panel. `required=False` checks only limit features."""
    key: str
    title: str
    ok: bool
    detail: str
    required: bool = True


def run_checks(include_engine: bool = True) -> list[Check]:
    """Current setup checks. Cheap and read-only; doesn't start the engine."""
    checks = []
    model = bundled_embedding_model()
    checks.append(Check("embedding", "Theme-finding model", model is not None,
                        "Installed with the app · shared by every project" if model else
                        "Missing from this installation. Reinstall the app to restore it."))
    names = model_pack.installed_models()
    if include_engine:
        status = get_engine().status
        running = status.state == "running"
        detail = status.message or ("Running" if running else "Not started")
        if running and status.source == "system":
            detail = "Using the Ollama already installed on this computer"
        checks.append(Check("engine", "Local AI engine", running or engine_binary() is not None,
                            detail, required=False))
    checks.append(Check("naming_model", "Theme-naming model", bool(names),
                        (", ".join(names) if names else
                         "Not installed. Themes use keyword names until you add a model pack."),
                        required=False))
    checks.append(Check("offline", "Offline mode", is_offline(),
                        "Nothing leaves this computer" if is_offline() else "Offline guard is off"))
    return checks


def summary(checks: list[Check]) -> str:
    """'ready' (all ok), 'limited' (only optional checks failing) or 'needs_repair'."""
    if all(check.ok for check in checks):
        return "ready"
    if all(check.ok for check in checks if check.required):
        return "limited"
    return "needs_repair"
