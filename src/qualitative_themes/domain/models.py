"""Stable identities and project values; no GUI or persistence imports."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    directory: Path
    created_at: str
