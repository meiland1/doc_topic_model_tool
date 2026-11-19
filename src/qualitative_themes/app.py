"""Application entry point.

Usage:
    doc-topic-model-tool                         open the app
    doc-topic-model-tool --self-test             check the installation (used by the installer)
    doc-topic-model-tool --install-model-pack F  install a theme-naming model pack offline

Startup order matters: offline flags are set at import time, before any
ML library loads. run_gui() then builds MainWindow, which opens the start
screen (gui/hub.py) and starts the engine in the background. The engine
is always stopped on exit.
"""
import argparse
import json
import sys

# Offline by construction: set before any ML library can be imported.
from qualitative_themes.runtime.offline import enable_offline_mode

enable_offline_mode()


def self_test() -> int:
    """Check the embedding model, bundled packs and engine; print JSON results.

    Exit code 0 when embedding works and the engine is running or simply not
    bundled. A missing naming model doesn't fail the test.
    """
    from qualitative_themes.analysis.encoder import load_encoder
    from qualitative_themes.analysis.settings import AnalysisSettings
    from qualitative_themes.runtime.engine import get_engine
    from qualitative_themes.runtime import model_pack
    results = {}
    try:
        encoder = load_encoder(AnalysisSettings())
        vector = encoder.encode(["The clinic is far from the bus stop."], normalize_embeddings=True)
        results["embedding"] = {"ok": len(vector[0]) > 0}
    except Exception as exc:
        results["embedding"] = {"ok": False, "error": str(exc)}
    try:
        model_pack.install_bundled_packs()
    except Exception as exc:
        results["model_pack"] = {"ok": False, "error": str(exc)}
    engine = get_engine()
    status = engine.start(timeout=60)
    results["engine"] = {"ok": status.state in {"running", "missing"}, "state": status.state, "message": status.message}
    results["naming_models"] = model_pack.installed_models()
    from qualitative_themes.analysis.settings import DEFAULT_NAMING_MODEL
    results["default_naming_model"] = {"name": DEFAULT_NAMING_MODEL,
                                       "installed": DEFAULT_NAMING_MODEL in results["naming_models"]}
    engine.stop()
    print(json.dumps(results, indent=2))
    return 0 if results["embedding"]["ok"] and results["engine"]["ok"] else 1


def install_pack(path) -> int:
    """CLI wrapper around install_model_pack(); used by the Windows installer."""
    from qualitative_themes.runtime.model_pack import install_model_pack
    try:
        info = install_model_pack(path, progress=print)
    except Exception as exc:
        print(f"Could not install the model pack: {exc}", file=sys.stderr)
        return 1
    print(f"Installed theme-naming model {info['model']}.")
    return 0


def run_gui() -> int:
    """Create the QApplication and MainWindow and run the event loop."""
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication
    from qualitative_themes.gui.fonts import configure_application_font
    from qualitative_themes.gui.main_window import MainWindow
    from qualitative_themes.gui.styling import apply_application_style
    from qualitative_themes.runtime.engine import get_engine
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("Topic Model Tool")
    apply_application_style(app)
    configure_application_font(app)
    window = MainWindow()
    window.start_local_services()
    window.show()
    try:
        return app.exec()
    finally:
        get_engine().stop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="doc-topic-model-tool")
    parser.add_argument("--self-test", action="store_true", help="check the installation and exit")
    parser.add_argument("--install-model-pack", metavar="FILE", help="install a .tmtpack theme-naming model")
    args, _unknown = parser.parse_known_args(argv)
    if args.self_test:
        return self_test()
    if args.install_model_pack:
        return install_pack(args.install_model_pack)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
