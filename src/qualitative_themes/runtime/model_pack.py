"""Theme-naming model packs: offline bundles of an Ollama model store.

A ``.tmtpack`` file is a zip archive:

    pack.json                      {"format": "tmt-model-pack-1", "model": "llama3.1:8b", ...}
    models/manifests/...           Ollama manifest(s) for the model
    models/blobs/sha256-...        the model's blobs

It is built on a connected machine with ``scripts/build_model_pack.py`` and
installed fully offline: the installer (Windows), the start screen's
"Add naming model…" button, or ``--install-model-pack <file>``.

GitHub limits each release file to 2 GiB, so a large pack (llama3.1:8b is about
4.9 GB) is split into numbered parts: ``name.tmtpack.001``, ``name.tmtpack.002``…
The parts are plain byte slices of one zip; joined in order they are the
``.tmtpack``. Installing reads the parts in place, so they never need to be
joined on disk. Pass any part (or the folder's ``.001`` file) to install.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from qualitative_themes.runtime.paths import bundled_model_packs, engine_models_dir

FORMAT = "tmt-model-pack-1"
PART_PATTERN = re.compile(r"^(?P<stem>.+\.tmtpack)\.(?P<index>\d{3})$")
# Space kept free beyond the model itself so the computer stays usable.
DISK_MARGIN = 512 * 1024 ** 2


class _JoinedParts(io.RawIOBase):
    """Read-only, seekable view of several files as one continuous stream."""

    def __init__(self, paths: list[Path]):
        super().__init__()
        self._files = [open(path, "rb") for path in paths]
        self._sizes = [Path(path).stat().st_size for path in paths]
        self._starts = []
        total = 0
        for size in self._sizes:
            self._starts.append(total)
            total += size
        self._length = total
        self._position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._length + offset
        else:
            raise ValueError("invalid whence")
        if position < 0:
            raise OSError("negative seek position")
        self._position = position
        return position

    def readinto(self, buffer):
        view = memoryview(buffer).cast("B")
        written = 0
        while written < len(view) and self._position < self._length:
            index = max(i for i, start in enumerate(self._starts) if start <= self._position)
            handle = self._files[index]
            handle.seek(self._position - self._starts[index])
            want = min(len(view) - written, self._sizes[index] - (self._position - self._starts[index]))
            chunk = handle.read(want)
            if not chunk:
                break
            view[written:written + len(chunk)] = chunk
            written += len(chunk)
            self._position += len(chunk)
        return written

    def close(self):
        for handle in self._files:
            handle.close()
        super().close()


def pack_parts(pack: Path) -> list[Path]:
    """The files that make up a pack: itself, or every numbered part in order."""
    pack = Path(pack)
    match = PART_PATTERN.match(pack.name)
    if not match:
        return [pack]
    stem = match.group("stem")
    parts = []
    index = 1
    while (candidate := pack.with_name(f"{stem}.{index:03d}")).is_file():
        parts.append(candidate)
        index += 1
    if not parts:
        raise ValueError(f"{stem}.001 is missing. Keep every part of the model pack in the same folder.")
    return parts


def display_name(pack: Path) -> str:
    match = PART_PATTERN.match(Path(pack).name)
    return match.group("stem") if match else Path(pack).name


def _open_archive(pack: Path) -> zipfile.ZipFile:
    parts = pack_parts(pack)
    try:
        if len(parts) == 1:
            return zipfile.ZipFile(parts[0])
        return zipfile.ZipFile(io.BufferedReader(_JoinedParts(parts), buffer_size=1024 * 1024))
    except zipfile.BadZipFile as exc:
        if len(parts) > 1 or PART_PATTERN.match(Path(pack).name):
            raise ValueError(f"{display_name(pack)} looks incomplete: found {len(parts)} part(s). "
                             "Copy every numbered part into the same folder and try again.") from exc
        raise ValueError(f"{Path(pack).name} is damaged or not a theme-naming model pack.") from exc


def is_pack_file(path: Path) -> bool:
    name = Path(path).name
    return name.endswith(".tmtpack") or bool(re.match(r"^.+\.tmtpack\.001$", name))


def read_pack_info(pack: Path) -> dict:
    with _open_archive(pack) as archive:
        try:
            info = json.loads(archive.read("pack.json"))
        except KeyError as exc:
            raise ValueError(f"{display_name(pack)} is not a theme-naming model pack.") from exc
    if info.get("format") != FORMAT or not isinstance(info.get("model"), str):
        raise ValueError(f"{display_name(pack)} is not a theme-naming model pack.")
    return info


def _safe_member(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "models":
        return None
    return PurePosixPath(*path.parts[1:]) if len(path.parts) > 1 else None


def install_model_pack(pack: Path, destination: Path | None = None, progress=None, disk_usage=shutil.disk_usage) -> dict:
    """Copy a pack's model files into the engine store. Safe to run twice."""
    progress = progress or (lambda message: None)
    destination = Path(destination or engine_models_dir())
    info = read_pack_info(pack)
    with _open_archive(pack) as archive:
        members = [(m, _safe_member(m.filename)) for m in archive.infolist() if not m.is_dir()]
        members = [(m, rel) for m, rel in members if rel is not None]
        pending = [(m, rel) for m, rel in members
                   if not ((t := destination.joinpath(*rel.parts)).is_file() and t.stat().st_size == m.file_size)]
        needed = sum(m.file_size for m, _ in pending)
        destination.mkdir(parents=True, exist_ok=True)
        free = disk_usage(destination).free
        if needed and free < needed + DISK_MARGIN:
            raise ValueError(f"Not enough disk space to add {info['model']}: it needs about "
                             f"{(needed + DISK_MARGIN) / 1024 ** 3:.1f} GB free and "
                             f"{free / 1024 ** 3:.1f} GB is available. Free some space and try again.")
        copied = 0
        for index, (member, relative) in enumerate(pending, 1):
            target = destination.joinpath(*relative.parts)
            progress(f"Installing {info['model']}: file {index} of {len(pending)} "
                     f"({copied / 1024 ** 3:.1f} of {needed / 1024 ** 3:.1f} GB)…")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".partial")
            try:
                # Reading to the end verifies the zip CRC, so a damaged part fails here.
                with archive.open(member) as source, temporary.open("wb") as out:
                    shutil.copyfileobj(source, out, 1024 * 1024)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            temporary.replace(target)
            copied += member.file_size
    return info


def installed_models(store: Path | None = None) -> list[str]:
    """Model names present in the engine store, read from manifests (no engine needed)."""
    root = Path(store or engine_models_dir(create=False)) / "manifests"
    if not root.is_dir():
        return []
    names = []
    for manifest in root.rglob("*"):
        if manifest.is_file():
            parts = manifest.relative_to(root).parts
            if len(parts) >= 2:
                name, tag = parts[-2], parts[-1]
                names.append(f"{name}:{tag}")
    return sorted(set(names))


def install_bundled_packs(progress=None) -> list[str]:
    installed = []
    for pack in bundled_model_packs():
        installed.append(install_model_pack(pack, progress=progress)["model"])
    return installed


def find_packs(folder: Path) -> list[Path]:
    """Single-file packs and the first part of split packs in a folder."""
    if not Path(folder).is_dir():
        return []
    return sorted(path for path in Path(folder).iterdir() if path.is_file() and is_pack_file(path))


def find_downloaded_packs() -> list[Path]:
    """Packs a researcher saved to Downloads or the Desktop, offered on the start screen."""
    found = []
    for folder in (Path.home() / "Downloads", Path.home() / "Desktop"):
        found.extend(find_packs(folder))
    return found
