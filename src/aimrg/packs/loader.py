"""Load content packs from disk and validate them, collecting every problem found.

Pack layout::

    <pack>/
    ├── pack.yaml            # PackManifest
    ├── sources.yaml         # SourcesFile
    ├── controls/*.yaml      # ControlsFile, one or more
    ├── tiering/*.yaml       # TieringProfile, one per file
    └── thresholds/*.yaml    # ThresholdsFile, optional
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from aimrg.packs.models import (
    Control,
    ControlsFile,
    Pack,
    PackManifest,
    Source,
    SourcesFile,
    Threshold,
    ThresholdsFile,
    TieringProfile,
)

M = TypeVar("M", bound=BaseModel)


@dataclass
class PackProblem:
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.message}"


@dataclass
class PackValidationError(Exception):
    pack_dir: str
    problems: list[PackProblem] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"pack at {self.pack_dir} has {len(self.problems)} problem(s):"]
        lines += [f"  - {p}" for p in self.problems]
        return "\n".join(lines)


class _Collector:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.problems: list[PackProblem] = []

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def add(self, path: Path, message: str) -> None:
        self.problems.append(PackProblem(self.rel(path), message))

    def read_yaml(self, path: Path) -> Any:
        try:
            with path.open(encoding="utf-8") as handle:
                return yaml.safe_load(handle)
        except FileNotFoundError:
            self.add(path, "file not found")
        except yaml.YAMLError as exc:
            self.add(path, f"invalid YAML: {exc}")
        return None

    def parse(self, path: Path, model: type[M]) -> M | None:
        data = self.read_yaml(path)
        if data is None:
            if path.exists():
                self.add(path, "file is empty")
            return None
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                self.add(path, f"{location}: {error['msg']}")
            return None


def _yaml_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])


def load_pack(pack_dir: str | Path) -> Pack:
    """Load and validate a pack. Raises :class:`PackValidationError` listing all problems."""
    root = Path(pack_dir).resolve()
    collect = _Collector(root)
    if not root.is_dir():
        collect.add(root, "pack directory does not exist")
        raise PackValidationError(str(root), collect.problems)

    manifest = collect.parse(root / "pack.yaml", PackManifest)
    sources_file = collect.parse(root / "sources.yaml", SourcesFile)

    controls: list[Control] = []
    control_files = _yaml_files(root / "controls")
    if not control_files:
        collect.add(root / "controls", "no control files found")
    control_origin: dict[str, Path] = {}
    for path in control_files:
        parsed = collect.parse(path, ControlsFile)
        if parsed is None:
            continue
        for control in parsed.controls:
            if control.id in control_origin:
                first = collect.rel(control_origin[control.id])
                collect.add(path, f"duplicate control id {control.id} (first defined in {first})")
                continue
            control_origin[control.id] = path
            controls.append(control)

    profiles: list[TieringProfile] = []
    profile_files = _yaml_files(root / "tiering")
    if not profile_files:
        collect.add(root / "tiering", "no tiering profiles found")
    for path in profile_files:
        profile = collect.parse(path, TieringProfile)
        if profile is None:
            continue
        if any(p.id == profile.id for p in profiles):
            collect.add(path, f"duplicate tiering profile id {profile.id!r}")
            continue
        profiles.append(profile)

    thresholds: dict[str, Threshold] = {}
    for path in _yaml_files(root / "thresholds"):
        parsed_thresholds = collect.parse(path, ThresholdsFile)
        if parsed_thresholds is None:
            continue
        for key, value in parsed_thresholds.thresholds.items():
            if key in thresholds:
                collect.add(path, f"duplicate threshold {key!r}")
                continue
            thresholds[key] = value

    sources: list[Source] = []
    if sources_file is not None:
        seen: set[str] = set()
        for source in sources_file.sources:
            if source.id in seen:
                collect.add(root / "sources.yaml", f"duplicate source id {source.id}")
                continue
            seen.add(source.id)
            sources.append(source)
        for control in controls:
            for reference in control.references:
                if reference.source not in seen:
                    collect.add(
                        control_origin[control.id],
                        f"{control.id}: reference to unknown source {reference.source!r}",
                    )

    if collect.problems or manifest is None:
        raise PackValidationError(str(root), collect.problems)

    return Pack(
        manifest=manifest,
        sources=sources,
        controls=controls,
        tiering_profiles=profiles,
        thresholds=thresholds,
    )


def builtin_packs_dir() -> Path:
    """Directory holding the packs shipped with AI-MRG.

    In an installed wheel the packs live at ``aimrg/_packs``; in a source checkout
    (including editable installs) they live in the repository's ``packs/`` folder.
    """
    bundled = Path(str(resources.files("aimrg"))) / "_packs"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().parents[3] / "packs"


def builtin_pack_names() -> list[str]:
    base = builtin_packs_dir()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "pack.yaml").is_file())


def load_builtin_pack(name: str) -> Pack:
    if name not in builtin_pack_names():
        raise KeyError(f"no built-in pack named {name!r}; available: {builtin_pack_names()}")
    return load_pack(builtin_packs_dir() / name)
