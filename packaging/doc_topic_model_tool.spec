# PyInstaller spec — build with:  pyinstaller packaging/doc_topic_model_tool.spec --noconfirm
# Run scripts/fetch_resources.py first so resources/ holds the model and engine.
# Set TMT_ALLOW_INCOMPLETE=1 only for local experiments without the bundled model/engine.
import os
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
APP_NAME = "Topic Model Tool"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

datas, binaries, hiddenimports = [], [], []
for package in ["sentence_transformers", "transformers", "tokenizers", "bertopic", "umap",
                "hdbscan", "pynndescent", "sklearn", "plotly", "fitz"]:
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

# Copy the stylesheet straight from the source tree so the build does not depend on
# the package being pip-installed (collect_data_files silently skips it otherwise).
datas.append((str(ROOT / "src" / "qualitative_themes" / "gui" / "styles"), "qualitative_themes/gui/styles"))

resources = ROOT / "resources"
engine_name = "ollama.exe" if sys.platform == "win32" else "ollama"
missing = [label for label, path in [
    ("embedding model (resources/models/embedding/config.json)", resources / "models" / "embedding" / "config.json"),
    (f"local AI engine (resources/engine/{engine_name})", resources / "engine" / engine_name),
] if not path.is_file()]
if missing:
    message = "Missing bundled resources: " + "; ".join(missing) + ". Run scripts/fetch_resources.py first."
    if os.environ.get("TMT_ALLOW_INCOMPLETE") != "1":
        raise SystemExit(message)
    print("WARNING: " + message, file=sys.stderr)
packs = resources / "model-packs"
if packs.is_dir() and sum(f.stat().st_size for f in packs.rglob("*") if f.is_file()) > 1024 ** 3:
    print("WARNING: resources/model-packs is over 1 GiB. Large naming models such as llama3.1:8b "
          "should ship beside the installer, not inside the app, or the installer will exceed "
          "GitHub's 2 GiB file limit.", file=sys.stderr)
for sub in ["models", "engine", "model-packs", "LICENSES"]:
    if (resources / sub).exists():
        datas.append((str(resources / sub), f"resources/{sub}"))

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["qualitative_themes.app"],
    excludes=["tkinter", "matplotlib", "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=str(ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.icns"))
    if (ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.icns")).exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        bundle_identifier="org.opensource.doctopicmodeltool",
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
