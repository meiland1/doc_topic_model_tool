"""Stable identities and project values; no GUI or persistence imports."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Project:
    """A project as the rest of the app sees it.

    `directory` is always resolved and absolute; everything for the project
    (project.sqlite, sources/, models/) lives under it.
    """
    id: str
    name: str
    directory: Path
    created_at: str
