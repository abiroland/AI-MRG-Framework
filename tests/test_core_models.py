from __future__ import annotations

import pytest
from pydantic import ValidationError

from aimrg.core import (
    AICategory,
    AssetRecord,
    Classification,
    EvidenceItem,
    EvidenceType,
    Finding,
    FindingStatus,
    GovernanceTrack,
    RiskAssessment,
    Severity,
    TierOverride,
    derive_track,
    verify_chain,
)


@pytest.mark.parametrize(
    ("is_model", "category", "expected"),
    [
        (True, AICategory.TRADITIONAL, GovernanceTrack.MODEL),
        (True, AICategory.ML_NON_GENERATIVE, GovernanceTrack.MODEL),
        (False, AICategory.TRADITIONAL, GovernanceTrack.NON_MODEL),
        (True, AICategory.GENERATIVE, GovernanceTrack.AI_SYSTEM),
        (False, AICategory.AGENTIC, GovernanceTrack.AI_SYSTEM),
    ],
)
def test_derive_track(is_model: bool, category: AICategory, expected: GovernanceTrack) -> None:
    assert derive_track(is_model_sr26_2=is_model, ai_category=category) is expected


def test_classification_from_answers_derives_track() -> None:
    c = Classification.from_answers(
        is_model_sr26_2=True,
        ai_category=AICategory.GENERATIVE,
        rationale="LLM drafting adverse-action letters",
        consequential_decision=True,
    )
    assert c.track is GovernanceTrack.AI_SYSTEM


def test_classification_rejects_inconsistent_track() -> None:
    with pytest.raises(ValidationError, match="inconsistent"):
        Classification(
            is_model_sr26_2=True,
            ai_category=AICategory.AGENTIC,
            rationale="x",
            track=GovernanceTrack.MODEL,
        )


def _asset(**overrides: object) -> AssetRecord:
    data: dict[str, object] = {
        "name": "Retail Card Underwriting v3",
        "owner": "credit-risk",
        "business_uses": ["credit_decision"],
        "ai_category": AICategory.ML_NON_GENERATIVE,
    }
    data.update(overrides)
    return AssetRecord.model_validate(data)


def test_asset_defaults() -> None:
    asset = _asset()
    assert asset.status.value == "intake"
    assert len(asset.id) == 32


def test_asset_requires_business_use() -> None:
    with pytest.raises(ValidationError):
        _asset(business_uses=[])


def test_asset_category_must_match_classification() -> None:
    classification = Classification.from_answers(
        is_model_sr26_2=True, ai_category=AICategory.TRADITIONAL, rationale="scorecard"
    )
    with pytest.raises(ValidationError, match="must match"):
        _asset(classification=classification)


def test_asset_cannot_depend_on_itself() -> None:
    with pytest.raises(ValidationError, match="itself"):
        _asset(id="abc", depends_on=["abc"])


def test_asset_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _asset(colour="blue")


def test_risk_assessment_effective_tier_uses_override() -> None:
    ra = RiskAssessment(
        profile_id="fin-us-banking/default",
        factor_scores={"exposure": 5},
        computed_score=0.5,
        computed_tier=2,
        assessed_by="mrm",
    )
    assert ra.tier == 2
    ra.override = TierOverride(tier=1, justification="new regulatory use", approved_by="cro")
    assert ra.tier == 1


def test_override_requires_justification() -> None:
    with pytest.raises(ValidationError):
        TierOverride(tier=1, justification="  ", approved_by="cro")


def _evidence(prev: EvidenceItem | None = None, **overrides: object) -> EvidenceItem:
    data: dict[str, object] = {
        "asset_id": "asset-1",
        "control_id": "FIN-VAL-003",
        "type": EvidenceType.CHECK_RESULT,
        "pack_version": "fin-us-banking@0.1.0-dev",
        "actor": "validator@bank",
        "prev_hash": prev.record_hash() if prev else None,
    }
    data.update(overrides)
    return EvidenceItem.model_validate(data)


def test_record_hash_is_deterministic_and_content_sensitive() -> None:
    item = _evidence()
    assert item.record_hash() == item.model_copy().record_hash()
    assert item.record_hash() != item.model_copy(update={"summary": "changed"}).record_hash()


def test_verify_chain_accepts_intact_chain() -> None:
    first = _evidence()
    second = _evidence(prev=first)
    third = _evidence(prev=second)
    assert verify_chain([first, second, third]) == []


def test_verify_chain_detects_tampering() -> None:
    first = _evidence()
    second = _evidence(prev=first)
    tampered_first = first.model_copy(update={"summary": "edited after the fact"})
    problems = verify_chain([tampered_first, second])
    assert len(problems) == 1
    assert "prev_hash does not match" in problems[0]


def test_verify_chain_detects_bad_start_and_mixed_assets() -> None:
    first = _evidence(prev_hash="0" * 64)
    other = _evidence(prev=first, asset_id="asset-2")
    problems = verify_chain([first, other])
    assert any("first item" in p for p in problems)
    assert any("different asset" in p for p in problems)


def test_evidence_rejects_bad_hash() -> None:
    with pytest.raises(ValidationError):
        _evidence(payload_sha256="not-a-hash")


def test_closed_finding_requires_remediation_evidence() -> None:
    base = {"asset_id": "a", "severity": Severity.HIGH, "title": "PSI breach", "owner": "dev"}
    with pytest.raises(ValidationError, match="remediation"):
        Finding.model_validate({**base, "status": FindingStatus.CLOSED})
    closed = Finding.model_validate(
        {**base, "status": FindingStatus.CLOSED, "remediation_evidence": ["ev-1"]}
    )
    assert closed.status is FindingStatus.CLOSED


def test_risk_accepted_finding_requires_acceptor() -> None:
    base = {"asset_id": "a", "severity": Severity.LOW, "title": "doc gap", "owner": "dev"}
    with pytest.raises(ValidationError, match="risk_acceptance_by"):
        Finding.model_validate({**base, "status": FindingStatus.RISK_ACCEPTED})
