"""Examination packages: everything an examiner or auditor needs, in one verifiable zip.

A package contains, for the selected assets:

* ``manifest.json``: what the package is, who produced it, the pack version, the audit
  result at export time, and which reports could not be rendered;
* ``records/``: asset records, evidence (JSON Lines, in chain order), findings and
  lifecycle events;
* ``artifacts/``: every stored artifact the evidence refers to (content-addressed);
* ``reports/``: the relevant reports rendered from the evidence, plus the inventory report;
* ``pack/``: a snapshot of the governance pack (controls, sources, thresholds, templates);
* ``SHA256SUMS``: a SHA-256 hash for every other file.

``verify_package`` recomputes the hashes and checks that nothing was added or removed.
To make the package tamper-evident beyond the archive itself, record the hash of
``SHA256SUMS`` (printed on export) somewhere independent, or sign that file with your
organization's signing tools.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aimrg import __version__
from aimrg.core.models import AssetRecord, GovernanceTrack
from aimrg.registry.store import ARTIFACT_SCHEME, Registry, RegistryError, _describe
from aimrg.reports.render import render

FORMAT = "aimrg-examination-package"
FORMAT_VERSION = 1
SUMS = "SHA256SUMS"


@dataclass
class ExportSummary:
    path: Path
    assets: int
    files: int
    sums_sha256: str
    audit_ok: bool
    skipped_reports: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "asset"


def reports_for(asset: AssetRecord) -> list[str]:
    """The asset-level reports relevant to ``asset``."""
    if asset.classification is None or asset.risk_assessment is None:
        return []
    c = asset.classification
    names = ["ai_system_record" if c.track is GovernanceTrack.AI_SYSTEM else "validation_report"]
    if "credit_decision" in asset.business_uses:
        names += ["fair_lending_report", "adverse_action_reasons"]
    if c.third_party or asset.vendor:
        names.append("vendor_assessment")
    if c.consequential_decision and "US-CO" in c.jurisdictions:
        names.append("colorado_admt_notices")
    return names


def _dump(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def export_package(
    registry: Registry, out_path: str | Path, assets: list[str] | None = None
) -> ExportSummary:
    """Write an examination package for ``assets`` (names or ids; default: all)."""
    selected = [registry.get(ref) for ref in assets] if assets else registry.assets()
    if not selected:
        raise RegistryError("there are no assets to export")
    files: dict[str, bytes] = {}
    skipped: list[str] = []
    artifacts: set[str] = set()
    used_slugs: set[str] = set()

    for asset in selected:
        slug = _slug(asset.name)
        if slug in used_slugs:
            slug = f"{slug}-{asset.id[:8]}"
        used_slugs.add(slug)
        evidence = registry.evidence(asset.id)
        files[f"records/assets/{slug}.json"] = _dump(asset.model_dump(mode="json"))
        files[f"records/evidence/{slug}.jsonl"] = "".join(
            e.model_dump_json() + "\n" for e in evidence
        ).encode("utf-8")
        files[f"records/findings/{slug}.json"] = _dump(
            [f.model_dump(mode="json") for f in registry.findings(asset.id)]
        )
        files[f"records/events/{slug}.json"] = _dump(
            [e.model_dump(mode="json") for e in registry.events(asset.id)]
        )
        artifacts |= {
            e.payload_uri
            for e in evidence
            if e.payload_uri and e.payload_uri.startswith(ARTIFACT_SCHEME)
        }
        for template in reports_for(asset):
            try:
                files[f"reports/{slug}/{template}.md"] = render(
                    registry, template, asset.id
                ).encode("utf-8")
            except RegistryError as exc:  # pragma: no cover - templates are tested
                skipped.append(f"{asset.name}: {template}: {exc}")
        if not reports_for(asset):
            skipped.append(f"{asset.name}: not classified and tiered; no reports rendered")
    files["reports/inventory_report.md"] = render(registry, "inventory_report").encode("utf-8")

    missing_artifacts = []
    for uri in sorted(artifacts):
        path = registry.artifact_path(uri)
        if path.is_file():
            files[f"artifacts/{uri.removeprefix(ARTIFACT_SCHEME)}"] = path.read_bytes()
        else:
            missing_artifacts.append(uri)

    pack_root = registry.pack.root
    if pack_root:
        for path in sorted(Path(pack_root).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[f"pack/{path.relative_to(pack_root).as_posix()}"] = path.read_bytes()

    audit = registry.verify()
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "aimrg_version": __version__,
        "pack": registry.pack.manifest.ref,
        "pack_reviewed": registry.pack.manifest.reviewed,
        "registry": {"backend": registry.backend, "location": _describe(registry.location)},
        "assets": [{"id": a.id, "name": a.name, "status": a.status.value} for a in selected],
        "audit": {
            "ok": audit.ok,
            "evidence_checked": audit.evidence_checked,
            "problems": audit.problems,
        },
        "missing_artifacts": missing_artifacts,
        "skipped_reports": skipped,
        "disclaimer": (
            "Produced by AI-MRG from recorded evidence. Not legal advice; does not by "
            "itself establish regulatory compliance."
        ),
    }
    files["manifest.json"] = _dump(manifest)

    sums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(files.items())
    ).encode("utf-8")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
        archive.writestr(SUMS, sums)
    return ExportSummary(
        path=out,
        assets=len(selected),
        files=len(files) + 1,
        sums_sha256=hashlib.sha256(sums).hexdigest(),
        audit_ok=audit.ok,
        skipped_reports=skipped,
    )


def verify_package(path: str | Path, expected_sums_sha256: str | None = None) -> list[str]:
    """Check a package's integrity. Returns a list of problems (empty = intact)."""
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError) as exc:
        return [f"cannot open package: {exc}"]
    with archive:
        names = set(archive.namelist())
        if SUMS not in names:
            return [f"{SUMS} is missing"]
        sums = archive.read(SUMS)
        if expected_sums_sha256 and hashlib.sha256(sums).hexdigest() != expected_sums_sha256:
            problems.append(f"{SUMS} does not match the expected hash")
        listed: dict[str, str] = {}
        for line in sums.decode("utf-8").splitlines():
            digest, _, name = line.partition("  ")
            listed[name] = digest
        for name, digest in listed.items():
            if name not in names:
                problems.append(f"missing file: {name}")
            elif hashlib.sha256(archive.read(name)).hexdigest() != digest:
                problems.append(f"altered file: {name}")
        for name in sorted(names - set(listed) - {SUMS}):
            problems.append(f"unlisted file: {name}")
        if "manifest.json" in names:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT:
                problems.append("not an AI-MRG examination package")
    return problems


def package_bytes(
    registry: Registry, assets: list[str] | None = None
) -> tuple[bytes, ExportSummary]:
    """Build a package in memory (used by the API)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        summary = export_package(registry, Path(tmp) / "package.zip", assets)
        content = summary.path.read_bytes()
    return content, summary


__all__ = [
    "ExportSummary",
    "export_package",
    "package_bytes",
    "reports_for",
    "verify_package",
]
