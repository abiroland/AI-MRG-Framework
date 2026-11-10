"""MLflow integration: bring registered models into the inventory, and push governance
status and reports back to MLflow.

Needs ``pip install "ai-mrg[mlflow]"`` (``mlflow-skinny`` is enough). Point it at your
tracking server with ``tracking_uri`` (or the ``MLFLOW_TRACKING_URI`` variable).

Mapping
-------
Registered-model tags tell AI-MRG how to register the model; anything missing falls back
to defaults you pass in:

=====================  =======================================================
MLflow tag             AI-MRG field
=====================  =======================================================
``aimrg.owner``        owner
``aimrg.business_uses`` business uses (comma-separated)
``aimrg.ai_category``  technique category
``aimrg.vendor``       vendor (for third-party models)
=====================  =======================================================

The asset records ``external_refs["mlflow.registered_model"]`` so later syncs find it.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aimrg.core.models import (
    AICategory,
    AssetRecord,
    EvidenceItem,
    EvidenceType,
    FindingStatus,
    GovernanceTrack,
)
from aimrg.packs.resolver import CoverageStatus, ResolverError, coverage
from aimrg.registry.store import Registry, RegistryError
from aimrg.reports.render import render

REF_MODEL = "mlflow.registered_model"
REF_RUN = "mlflow.run_id"
TAG_PREFIX = "aimrg."


class MlflowUnavailableError(RuntimeError):
    pass


def _client(tracking_uri: str | None) -> Any:
    try:
        from mlflow import MlflowClient
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise MlflowUnavailableError(
            'MLflow integration needs: pip install "ai-mrg[mlflow]"'
        ) from exc
    return MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)


@dataclass
class ImportResult:
    created: list[AssetRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _latest_run_id(client: Any, model_name: str) -> str | None:
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        return None
    latest = max(versions, key=lambda v: int(v.version))
    run_id = latest.run_id
    return str(run_id) if run_id else None


def import_registered_models(
    registry: Registry,
    *,
    tracking_uri: str | None = None,
    default_owner: str = "unassigned",
    default_uses: list[str] | None = None,
    default_category: AICategory | str = AICategory.ML_NON_GENERATIVE,
    name_filter: str | None = None,
) -> ImportResult:
    """Register every MLflow registered model not already in the inventory."""
    client = _client(tracking_uri)
    existing = {a.external_refs.get(REF_MODEL) for a in registry.assets()} | {
        a.name for a in registry.assets()
    }
    result = ImportResult()
    query = f"name LIKE '%{name_filter}%'" if name_filter else None
    for model in client.search_registered_models(filter_string=query):
        if model.name in existing:
            result.skipped.append(model.name)
            continue
        tags = dict(model.tags or {})
        uses = [u.strip() for u in tags.get("aimrg.business_uses", "").split(",") if u.strip()]
        refs = {REF_MODEL: model.name}
        run_id = _latest_run_id(client, model.name)
        if run_id:
            refs[REF_RUN] = run_id
        asset = AssetRecord(
            name=model.name,
            owner=tags.get("aimrg.owner", default_owner),
            business_uses=uses or list(default_uses or ["unspecified"]),
            ai_category=AICategory(tags.get("aimrg.ai_category", default_category)),
            description=model.description or "",
            vendor=tags.get("aimrg.vendor") or None,
            external_refs=refs,
        )
        result.created.append(registry.register(asset))
    return result


def governance_tags(registry: Registry, asset: AssetRecord) -> dict[str, str]:
    """A compact governance summary suitable for MLflow tags."""
    tags = {
        "aimrg.asset_id": asset.id,
        "aimrg.status": asset.status.value,
        "aimrg.pack": registry.pack.manifest.ref,
    }
    if asset.classification:
        tags["aimrg.track"] = asset.classification.track.value
    if asset.risk_assessment:
        tags["aimrg.tier"] = str(asset.risk_assessment.tier)
    try:
        items = coverage(registry.pack, asset, registry.evidence(asset.id))
        complete = sum(i.status is CoverageStatus.COMPLETE for i in items)
        tags["aimrg.controls_complete"] = f"{complete}/{len(items)}"
    except ResolverError:
        pass
    open_findings = [
        f
        for f in registry.findings(asset.id)
        if f.status in (FindingStatus.OPEN, FindingStatus.IN_REMEDIATION)
    ]
    tags["aimrg.open_findings"] = str(len(open_findings))
    return tags


@dataclass
class SyncResult:
    model: str | None
    run_id: str | None
    tags: dict[str, str]
    artifacts: list[str]


def sync_to_mlflow(
    registry: Registry,
    ref: str,
    *,
    tracking_uri: str | None = None,
    run_id: str | None = None,
    include_report: bool = True,
) -> SyncResult:
    """Write governance tags (and the asset's main report) to MLflow.

    Tags go on the registered model and on the run (``run_id``, or the run behind the
    model's latest version).
    """
    asset = registry.get(ref)
    model = asset.external_refs.get(REF_MODEL)
    client = _client(tracking_uri)
    run = run_id or (model and _latest_run_id(client, model)) or asset.external_refs.get(REF_RUN)
    if not model and not run:
        raise RegistryError(
            f"{asset.name!r} has no MLflow link; import it from MLflow or pass a run id"
        )
    tags = governance_tags(registry, asset)
    if model:
        for key, value in tags.items():
            client.set_registered_model_tag(model, key, value)
    artifacts: list[str] = []
    if run:
        for key, value in tags.items():
            client.set_tag(run, key, value)
        if include_report and asset.classification and asset.risk_assessment:
            template = (
                "ai_system_record"
                if asset.classification.track is GovernanceTrack.AI_SYSTEM
                else "validation_report"
            )
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"{template}.md"
                path.write_text(render(registry, template, asset.id), encoding="utf-8")
                client.log_artifact(run, str(path), artifact_path="aimrg")
            artifacts.append(f"aimrg/{template}.md")
    return SyncResult(model=model, run_id=run, tags=tags, artifacts=artifacts)


def record_run_as_evidence(
    registry: Registry,
    ref: str,
    control_id: str,
    run_id: str,
    *,
    actor: str,
    tracking_uri: str | None = None,
) -> EvidenceItem:
    """Snapshot an MLflow run (parameters, metrics, tags) as document evidence."""
    client = _client(tracking_uri)
    run = client.get_run(run_id)
    snapshot = {
        "mlflow_run_id": run_id,
        "experiment_id": run.info.experiment_id,
        "status": run.info.status,
        "start_time": run.info.start_time,
        "end_time": run.info.end_time,
        "params": dict(run.data.params),
        "metrics": dict(run.data.metrics),
        "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")},
    }
    uri, digest = registry.store_artifact(
        json.dumps(snapshot, indent=2, sort_keys=True, default=str).encode("utf-8"), ".json"
    )
    return registry.add_evidence(
        EvidenceItem(
            asset_id=ref,
            control_id=control_id,
            type=EvidenceType.DOCUMENT,
            summary=f"MLflow run {run_id[:12]}: {len(snapshot['metrics'])} metric(s), "
            f"{len(snapshot['params'])} parameter(s)",
            payload_uri=uri,
            payload_sha256=digest,
            pack_version=registry.pack.manifest.ref,
            actor=actor,
        )
    )
