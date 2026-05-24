"""Load capsule.yaml files from disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from capsule.schema import Capsule, from_dict


class CapsuleLoadError(Exception):
    """Raised when a capsule file cannot be parsed or validated."""


@dataclass
class LoadedCapsule:
    capsule: Capsule
    path: Path  # absolute path to capsule.yaml
    root: Path  # directory containing it

    @property
    def name(self) -> str:
        return self.capsule.name


def find_capsule_file(start: Path) -> Path:
    """Resolve a user-supplied path to a concrete capsule.yaml file."""
    start = start.expanduser().resolve()
    if start.is_file():
        return start
    if start.is_dir():
        candidate = start / "capsule.yaml"
        if candidate.is_file():
            return candidate
        candidate = start / "capsule.yml"
        if candidate.is_file():
            return candidate
        raise CapsuleLoadError(f"no capsule.yaml found in directory: {start}")
    raise CapsuleLoadError(f"path does not exist: {start}")


def load(path: str | Path) -> LoadedCapsule:
    """Load and validate a single capsule.yaml."""
    file_path = find_capsule_file(Path(path))
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CapsuleLoadError(f"{file_path}: YAML parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise CapsuleLoadError(f"{file_path}: top-level document must be a mapping")
    try:
        capsule = from_dict(raw)
    except ValidationError as exc:
        raise CapsuleLoadError(_format_validation_error(file_path, exc)) from exc
    return LoadedCapsule(capsule=capsule, path=file_path, root=file_path.parent)


def discover(root: Path) -> list[LoadedCapsule]:
    """Walk a directory tree, loading every capsule.yaml found."""
    root = root.expanduser().resolve()
    found: list[LoadedCapsule] = []
    for path in sorted(root.rglob("capsule.yaml")):
        # Skip files inside hidden or vendor-style directories.
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        found.append(load(path))
    return found


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: invalid capsule.yaml"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)
