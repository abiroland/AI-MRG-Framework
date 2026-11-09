"""Work out which controls apply to an asset, and how far its evidence covers them."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aimrg.core.models import AssetRecord, CheckOutcome, EvidenceItem, EvidenceType
from aimrg.packs.expr import compile_expression
from aimrg.packs.models import Control, ControlStage, Pack

_SEVERITY = [
    CheckOutcome.INFO,
    CheckOutcome.PASS,
    CheckOutcome.WARN,
    CheckOutcome.FAIL,
    CheckOutcome.ERROR,
]


def worst(outcomes: list[CheckOutcome]) -> CheckOutcome:
    """The most severe of ``outcomes`` (which must not be empty)."""
    return max(outcomes, key=_SEVERITY.index)


class ResolverError(ValueError):
    """Raised when an asset lacks what resolution needs (classification, tier)."""


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    FAILING = "failing"
    """All evidence types present, but the latest automated check failed."""


@dataclass
class ControlCoverage:
    control: Control
    evidence: list[EvidenceItem] = field(default_factory=list)
    missing: list[EvidenceType] = field(default_factory=list)
    status: CoverageStatus = CoverageStatus.MISSING
    latest_outcome: CheckOutcome | None = None


def context_for(asset: AssetRecord) -> dict[str, Any]:
    """Variables for ``applies_when`` expressions (see ``aimrg.packs.expr.VARIABLES``)."""
    if asset.classification is None:
        raise ResolverError(f"{asset.name!r} has not been classified")
    if asset.risk_assessment is None:
        raise ResolverError(f"{asset.name!r} has not been risk-tiered")
    c = asset.classification
    return {
        "track": c.track.value,
        "tier": asset.risk_assessment.tier,
        "ai_category": asset.ai_category.value,
        "uses": list(asset.business_uses),
        "consequential_decision": c.consequential_decision,
        "third_party": c.third_party or asset.vendor is not None,
        "jurisdictions": list(c.jurisdictions),
        "stage": asset.status.value,
    }


def required_controls(
    pack: Pack, asset: AssetRecord, stage: ControlStage | str | None = None
) -> list[Control]:
    """Controls whose ``applies_when`` holds for ``asset``, optionally for one stage."""
    context = context_for(asset)
    wanted = ControlStage(stage) if stage is not None else None
    selected = [
        c
        for c in pack.controls
        if (wanted is None or c.lifecycle_stage == wanted)
        and compile_expression(c.applies_when).evaluate(context)
    ]
    order = {s: i for i, s in enumerate(ControlStage)}
    return sorted(selected, key=lambda c: (order[c.lifecycle_stage], c.id))


def coverage(
    pack: Pack,
    asset: AssetRecord,
    evidence: list[EvidenceItem],
    stage: ControlStage | str | None = None,
) -> list[ControlCoverage]:
    """For each required control, which evidence exists and what is still missing."""
    by_control: dict[str, list[EvidenceItem]] = {}
    for item in evidence:
        if item.asset_id == asset.id:
            by_control.setdefault(item.control_id, []).append(item)

    results: list[ControlCoverage] = []
    for control in required_controls(pack, asset, stage):
        items = by_control.get(control.id, [])
        present = {i.type for i in items}
        missing = [t for t in control.evidence_required if t not in present]
        latest_by_check: dict[str, CheckOutcome] = {}
        for item in items:
            if item.type is EvidenceType.CHECK_RESULT and item.outcome is not None:
                latest_by_check[item.check_id or item.id] = item.outcome
        latest = worst(list(latest_by_check.values())) if latest_by_check else None
        if not items:
            status = CoverageStatus.MISSING
        elif missing:
            status = CoverageStatus.PARTIAL
        elif latest in (CheckOutcome.FAIL, CheckOutcome.ERROR):
            status = CoverageStatus.FAILING
        else:
            status = CoverageStatus.COMPLETE
        results.append(
            ControlCoverage(
                control=control,
                evidence=items,
                missing=missing,
                status=status,
                latest_outcome=latest,
            )
        )
    return results
