from __future__ import annotations

import pytest
from conftest import make_model_asset

from aimrg.core.classify import QUESTIONS, classify
from aimrg.core.models import (
    AICategory,
    AssetRecord,
    CheckOutcome,
    EvidenceItem,
    EvidenceType,
    GovernanceTrack,
    TierOverride,
)
from aimrg.packs.resolver import (
    CoverageStatus,
    ResolverError,
    context_for,
    coverage,
    required_controls,
)
from aimrg.registry import Registry
from aimrg.tiering import aggregate_flags, assess_asset


def test_classify_normalizes_jurisdictions() -> None:
    c = classify(
        ai_category="agentic",
        is_model_sr26_2=False,
        rationale="agent",
        jurisdictions=[" us ", "us-co", ""],
    )
    assert c.track is GovernanceTrack.AI_SYSTEM
    assert c.jurisdictions == ["US", "US-CO"]
    assert {q.key for q in QUESTIONS} >= {"is_model_sr26_2", "rationale"}


def test_assess_with_override_and_aggregate_flags(registry: Registry) -> None:
    upstream = registry.register(
        AssetRecord(
            name="Feed",
            owner="data",
            business_uses=["feature_engineering"],
            ai_category=AICategory.TRADITIONAL,
        )
    )
    first = make_model_asset(registry, "Model A")
    second = registry.register(
        AssetRecord(
            name="Model B",
            owner="risk",
            business_uses=["pricing"],
            ai_category=AICategory.TRADITIONAL,
            depends_on=[upstream.id],
        )
    )
    registry.save(registry.get(first.id).model_copy(update={"depends_on": [upstream.id]}))
    registry.set_classification(
        second.id, classify(ai_category="traditional", is_model_sr26_2=True, rationale="r")
    )
    assessed = assess_asset(
        registry,
        second.id,
        {"complexity": 1, "assumptions": 1, "data_quality": 1, "exposure": 1, "purpose": 1},
        assessed_by="v",
        override=TierOverride(tier=2, justification="regulatory use", approved_by="cro"),
    )
    ra = assessed.risk_assessment
    assert ra is not None
    assert ra.computed_tier == 4 and ra.tier == 2
    assert any("shares upstream asset 'Feed'" in f for f in ra.aggregate_flags)
    assert any("feeds 2 downstream" in f for f in aggregate_flags(registry, registry.get("Feed")))


def test_context_requires_classification_and_tier(registry: Registry) -> None:
    bare = registry.register(
        AssetRecord(name="Bare", owner="o", business_uses=["x"], ai_category=AICategory.TRADITIONAL)
    )
    with pytest.raises(ResolverError, match="classified"):
        context_for(bare)
    registry.set_classification(
        bare.id, classify(ai_category="traditional", is_model_sr26_2=True, rationale="r")
    )
    with pytest.raises(ResolverError, match="tiered"):
        context_for(registry.get(bare.id))


def test_required_controls_for_tier1_credit_model(registry: Registry) -> None:
    asset = make_model_asset(registry)
    ids = {c.id for c in required_controls(registry.pack, asset)}
    assert {"FIN-VAL-001", "FIN-VAL-003", "FIN-CPR-001", "FIN-MON-001"} <= ids
    assert not any(i.startswith("FIN-AIS") for i in ids)
    assert "FIN-CPR-005" not in ids  # not a Colorado decision
    validation = required_controls(registry.pack, asset, stage="validation")
    assert {c.lifecycle_stage.value for c in validation} == {"validation"}


def test_vendor_and_colorado_controls(registry: Registry) -> None:
    asset = make_model_asset(registry, jurisdictions=["US", "US-CO"], third_party=True)
    ids = {c.id for c in required_controls(registry.pack, asset)}
    assert {"FIN-CPR-005", "FIN-TPM-001", "FIN-TPM-002"} <= ids


def test_coverage_statuses(registry: Registry) -> None:
    asset = make_model_asset(registry)
    ref = registry.pack.manifest.ref

    def add(
        control: str,
        kind: EvidenceType,
        outcome: CheckOutcome | None = None,
        check: str | None = None,
    ) -> None:
        registry.add_evidence(
            EvidenceItem(
                asset_id=asset.id,
                control_id=control,
                type=kind,
                outcome=outcome,
                check_id=check,
                pack_version=ref,
                actor="t",
            )
        )

    add("FIN-VAL-003", EvidenceType.CHECK_RESULT, CheckOutcome.PASS, "performance.out_of_time")
    add("FIN-VAL-003", EvidenceType.ATTESTATION)
    add(
        "FIN-MON-001",
        EvidenceType.CHECK_RESULT,
        CheckOutcome.FAIL,
        "performance.discrimination_drift",
    )
    add(
        "FIN-MON-001",
        EvidenceType.CHECK_RESULT,
        CheckOutcome.PASS,
        "stability.population_stability",
    )
    add("FIN-VAL-001", EvidenceType.DOCUMENT)

    status = {c.control.id: c for c in coverage(registry.pack, asset, registry.evidence(asset.id))}
    assert status["FIN-VAL-003"].status is CoverageStatus.COMPLETE
    assert status["FIN-MON-001"].status is CoverageStatus.FAILING
    assert status["FIN-MON-001"].latest_outcome is CheckOutcome.FAIL
    assert status["FIN-VAL-001"].status is CoverageStatus.PARTIAL
    assert status["FIN-VAL-001"].missing == [EvidenceType.ATTESTATION]
    assert status["FIN-VAL-002"].status is CoverageStatus.MISSING

    # A later passing run of the failing check clears the failure.
    add(
        "FIN-MON-001",
        EvidenceType.CHECK_RESULT,
        CheckOutcome.PASS,
        "performance.discrimination_drift",
    )
    status = {c.control.id: c for c in coverage(registry.pack, asset, registry.evidence(asset.id))}
    assert status["FIN-MON-001"].status is CoverageStatus.COMPLETE
