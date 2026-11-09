"""Export JSON Schemas for the public data contracts."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from aimrg.core.models import AssetRecord, EvidenceItem, Finding, RiskAssessment
from aimrg.packs.models import (
    ControlsFile,
    PackManifest,
    SourcesFile,
    ThresholdsFile,
    TieringProfile,
)

SCHEMA_BASE_URL = "https://raw.githubusercontent.com/abiroland/ai-mrg-framework/main/schemas"

#: File name -> model. Pack-file schemas can be referenced from YAML editors via
#: ``# yaml-language-server: $schema=<SCHEMA_BASE_URL>/<file>``.
SCHEMAS: dict[str, type[BaseModel]] = {
    "asset-record.schema.json": AssetRecord,
    "risk-assessment.schema.json": RiskAssessment,
    "evidence-item.schema.json": EvidenceItem,
    "finding.schema.json": Finding,
    "pack-manifest.schema.json": PackManifest,
    "pack-sources.schema.json": SourcesFile,
    "pack-controls.schema.json": ControlsFile,
    "pack-tiering-profile.schema.json": TieringProfile,
    "pack-thresholds.schema.json": ThresholdsFile,
}


def render_schemas() -> dict[str, str]:
    rendered: dict[str, str] = {}
    for filename, model in SCHEMAS.items():
        schema = model.model_json_schema()
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{SCHEMA_BASE_URL}/{filename}",
            **schema,
        }
        rendered[filename] = json.dumps(schema, indent=2, sort_keys=False) + "\n"
    return rendered


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, text in render_schemas().items():
        path = out_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def stale_schemas(out_dir: Path) -> list[str]:
    """Names of schema files in ``out_dir`` that are missing or out of date."""
    stale: list[str] = []
    for filename, text in render_schemas().items():
        path = out_dir / filename
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(filename)
    return stale
