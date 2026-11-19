# Building the installers

Before publishing, follow [the local release handoff and readiness plan](LOCAL_RELEASE_HANDOFF.md).
The scripts below are existing build scaffolding; the current source has not yet
passed clean-machine Windows and macOS installer acceptance checks.

Researchers download finished installers from GitHub Releases. This page is for
maintainers. Everything here is free: PyInstaller, Inno Setup, ad-hoc signing,
GitHub Actions and GitHub Releases (public repositories).

## Automatic (recommended)

Push a version tag and GitHub Actions builds everything:

```bash
git tag v0.2.0
git push origin v0.2.0
```

`.github/workflows/release.yml` then:

1. runs the test suite;
2. builds the Windows installer (`TopicModelTool-Setup-<version>.exe`) with Inno Setup;
3. builds macOS disk images for Apple silicon and Intel, ad-hoc signed;
4. builds the theme-naming model pack once, for both platforms: `llama3.1:8b`
   (~4.9 GB) written as `naming-model-llama3.1-8b.tmtpack.001`, `.002`, `.003`
   plus its license text;
5. refuses any file of 2 GiB or more, then publishes a release with all files
   and `SHA256SUMS.txt`.

Each release file must be under GitHub's 2 GiB limit. That is why the naming
model ships beside the installer rather than inside it, and why
`build_model_pack.py` splits packs into parts of at most 1.9 GiB. The app and the
Windows installer read split parts directly; nobody needs to join them.

## By hand

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # Windows: keeps the app small
pip install -e ".[analysis,build]"
python scripts/fetch_resources.py                      # embedding model + engine for this OS
pyinstaller packaging/doc_topic_model_tool.spec --noconfirm   # fails if resources/ is incomplete
# Windows:  iscc /DAppVersion=0.2.1 packaging\windows\installer.iss
# macOS:    packaging/macos/build_dmg.sh 0.2.1 arm64
python scripts/build_model_pack.py --license "Llama 3.1 Community License" --license-file LICENSE-llama3.1.txt
```

`build_model_pack.py` defaults to `llama3.1:8b`, needs about 10 GB free while
building, and writes split parts. Use `--single-file` for a USB kit or for hosting
without a per-file limit, or `--model` for a different Ollama model.

### Engine size

The Ollama Windows download is ~1.9 GB, mostly GPU libraries (`cuda_v12` alone is
~2.3 GB unpacked). `fetch_resources.py --engine-gpu standard` (the default) drops
`cuda_v12` and keeps CUDA 13, Vulkan and CPU, which keeps the installer under
2 GiB. NVIDIA drivers too old for CUDA 13 fall back to Vulkan or CPU. Use
`--engine-gpu all` for an unpruned USB build, `--engine-gpu cpu` for the smallest.

### Offline kit layout

```
TopicModelTool-Setup-0.2.1.exe
naming-model-llama3.1-8b.tmtpack.001
naming-model-llama3.1-8b.tmtpack.002
naming-model-llama3.1-8b.tmtpack.003
naming-model-llama3.1-8b-LICENSE.txt
```

On Windows the installer installs the model from beside itself. On macOS,
researchers copy the parts into Downloads and choose **Add naming model…**. The
model is copied once into the app data folder and shared by every project; it is
preselected for naming whenever it is installed.

## What gets bundled

| Item | Source | License |
| --- | --- | --- |
| Theme-finding model `all-MiniLM-L6-v2` | Hugging Face, pinned revision in `settings.py` | Apache 2.0 |
| Local AI engine (Ollama) | GitHub releases, version in `scripts/fetch_resources.py` | MIT |
| Theme-naming model (optional pack) | Ollama library | Default `llama3.1:8b`: Llama 3.1 Community License — ship the license text and show "Built with Llama" |

License notices are copied to `resources/LICENSES/` and shipped inside the app.

## Before the first public release

- Add a `LICENSE` file for the project itself.
- Ollama 0.12.11 asset names were confirmed (`ollama-windows-amd64.zip`,
  `ollama-darwin.tgz`, `ollama-linux-amd64.tgz`); recheck if you change the pin.
- Check the final installer size (must be under 2 GiB) and test naming on a
  16 GB and an 8 GB machine.
- Install on a clean Windows and Mac with Wi-Fi off and run the sample project.

## Source checkout without installers

Developers can still run from source. Without `resources/engine`, the app uses an
Ollama already running on the standard port; without `resources/models/embedding`,
it falls back to a project's legacy `models/weights` folder.
