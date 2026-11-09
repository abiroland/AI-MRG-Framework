"""Bulk import of assets from CSV or YAML.

Most institutions already keep a spreadsheet inventory; importing it is the fastest way
to get started.

CSV columns (header row required):

=============== ========= =====================================================
column          required  notes
=============== ========= =====================================================
name            yes       unique
owner           yes
business_uses   yes       separate several with ``;``
ai_category     yes       traditional | ml_non_generative | generative | agentic
description     no
vendor          no        blank for in-house
depends_on      no        names of upstream assets, separated by ``;``
=============== ========= =====================================================

YAML: a list of mappings with the same keys (lists may be given as YAML lists).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aimrg.core.models import AssetRecord
from aimrg.registry.store import Registry, RegistryError

_LIST_FIELDS = ("business_uses", "depends_on")


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(";") if part.strip()]


def read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise RegistryError(f"{path}: expected a list of assets")
        return [dict(row) for row in data]
    raise RegistryError(f"{path}: unsupported file type (use .csv, .yaml or .yml)")


def import_assets(registry: Registry, path: str | Path) -> list[AssetRecord]:
    """Validate every row first, then register all of them (all-or-nothing)."""
    source = Path(path)
    rows = read_rows(source)
    errors: list[str] = []
    staged: list[tuple[AssetRecord, list[str]]] = []
    existing = {a.name for a in registry.assets()}
    names_in_file: set[str] = set()

    for line, row in enumerate(rows, start=2 if source.suffix.lower() == ".csv" else 1):
        data = {k.strip(): v for k, v in row.items() if k and v not in (None, "")}
        upstream = _split(data.pop("depends_on", None))
        for field in _LIST_FIELDS:
            if field in data:
                data[field] = _split(data[field])
        try:
            asset = AssetRecord.model_validate(data)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                errors.append(f"row {line}: {loc}: {err['msg']}")
            continue
        if asset.name in existing or asset.name in names_in_file:
            errors.append(f"row {line}: duplicate asset name {asset.name!r}")
            continue
        names_in_file.add(asset.name)
        staged.append((asset, upstream))

    known = existing | names_in_file
    for asset, upstream in staged:
        missing = [u for u in upstream if u not in known]
        if missing:
            errors.append(f"{asset.name}: unknown upstream asset(s) {missing}")
    if errors:
        raise RegistryError("import failed; nothing was imported:\n  " + "\n  ".join(errors))

    # Order so upstream assets are registered before their dependents; detect cycles first.
    ordered: list[tuple[AssetRecord, list[str]]] = []
    placed = set(existing)
    pending = list(staged)
    while pending:
        ready = [(a, up) for a, up in pending if all(u in placed for u in up)]
        if not ready:
            cycle = sorted(a.name for a, _ in pending)
            raise RegistryError(f"import failed; nothing was imported: dependency cycle {cycle}")
        for entry in ready:
            ordered.append(entry)
            placed.add(entry[0].name)
            pending.remove(entry)

    ids = {a.name: a.id for a in registry.assets()} | {a.name: a.id for a, _ in staged}
    return [
        registry.register(asset.model_copy(update={"depends_on": [ids[u] for u in upstream]}))
        for asset, upstream in ordered
    ]
