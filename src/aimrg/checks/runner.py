"""Run a control's checks for an asset and record the results as evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from aimrg.checks.base import CheckContext, CheckResult, run_check
from aimrg.core.models import (
    CheckOutcome,
    EvidenceItem,
    EvidenceType,
    Finding,
    Severity,
)
from aimrg.findings import default_due_date
from aimrg.registry.store import Registry, RegistryError


@dataclass
class CheckRun:
    check_id: str
    result: CheckResult
    evidence: EvidenceItem | None
    finding: Finding | None


def run_control_checks(
    registry: Registry,
    asset_ref: str,
    control_id: str,
    datasets: dict[str, pd.DataFrame],
    *,
    actor: str,
    target: str | None = None,
    score: str | None = None,
    features: list[str] | None = None,
    params: dict[str, Any] | None = None,
    open_findings: bool = True,
) -> list[CheckRun]:
    """Run every automated check defined by ``control_id``.

    Results from checks that ran (pass, warn, fail or info) are stored as artifacts and
    recorded as ``check_result`` evidence. Checks that could not run (``error``) are
    returned but not recorded. A failing check opens a finding unless
    ``open_findings`` is False.
    """
    asset = registry.get(asset_ref)
    pack = registry.pack
    try:
        control = pack.control(control_id)
    except KeyError as exc:
        raise RegistryError(f"unknown control {control_id!r} in {pack.manifest.ref}") from exc
    if not control.checks:
        raise RegistryError(f"{control_id} has no automated checks")

    tier = asset.risk_assessment.tier if asset.risk_assessment else None
    runs: list[CheckRun] = []
    for spec in control.checks:
        context = CheckContext(
            datasets=datasets,
            target=target,
            score=score,
            features=features,
            params={**spec.params, **(params or {})},
            thresholds={**pack.thresholds, **spec.thresholds},
        )
        result = run_check(spec.id, context)
        if result.outcome is CheckOutcome.ERROR:
            runs.append(CheckRun(spec.id, result, None, None))
            continue

        payload = {
            "asset": {"id": asset.id, "name": asset.name},
            "control_id": control.id,
            "pack": pack.manifest.ref,
            "result": result.model_dump(mode="json"),
        }
        uri, digest = registry.store_json_artifact(payload)
        evidence = registry.add_evidence(
            EvidenceItem(
                asset_id=asset.id,
                control_id=control.id,
                type=EvidenceType.CHECK_RESULT,
                summary=f"{spec.id}: {result.summary}",
                check_id=spec.id,
                outcome=result.outcome,
                payload_uri=uri,
                payload_sha256=digest,
                pack_version=pack.manifest.ref,
                actor=actor,
            )
        )
        finding = None
        if open_findings and result.outcome is CheckOutcome.FAIL:
            severity = Severity.HIGH if tier is not None and tier <= 2 else Severity.MEDIUM
            finding = registry.add_finding(
                Finding(
                    asset_id=asset.id,
                    control_id=control.id,
                    severity=severity,
                    due_date=default_due_date(severity),
                    title=f"{spec.id} failed ({control.id})",
                    description=result.summary,
                    owner=asset.owner,
                )
            )
        runs.append(CheckRun(spec.id, result, evidence, finding))
    return runs
