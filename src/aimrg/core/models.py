"""Domain models for governed assets, risk assessments, evidence and findings.

These models are the stable contract between the SDK, CLI, API and storage layers.
Their JSON Schemas are published under ``schemas/`` (see ``aimrg schema export``).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- Enumerations -------------------------------------------------------------


class AICategory(StrEnum):
    """What kind of technique the asset uses."""

    TRADITIONAL = "traditional"
    """Statistical, economic or financial models (e.g. logistic scorecards)."""
    ML_NON_GENERATIVE = "ml_non_generative"
    """Machine learning that is neither generative nor agentic (e.g. gradient boosting)."""
    GENERATIVE = "generative"
    """Generative AI, such as large language models."""
    AGENTIC = "agentic"
    """AI systems that plan and take actions with some autonomy."""


class GovernanceTrack(StrEnum):
    """Which governance track applies."""

    MODEL = "model"
    """A model within the scope of SR 26-2."""
    AI_SYSTEM = "ai_system"
    """Generative or agentic AI, which SR 26-2 excludes; governed via NIST AI RMF."""
    NON_MODEL = "non_model"
    """Tools that are neither (e.g. deterministic rules); inventoried, lightly governed."""


class LifecycleStatus(StrEnum):
    INTAKE = "intake"
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    APPROVED = "approved"
    APPROVED_WITH_LIMITS = "approved_with_limits"
    REJECTED = "rejected"
    IN_PRODUCTION = "in_production"
    CHANGE_REQUESTED = "change_requested"
    RETIRED = "retired"


class EvidenceType(StrEnum):
    CHECK_RESULT = "check_result"
    DOCUMENT = "document"
    ATTESTATION = "attestation"
    APPROVAL = "approval"


class CheckOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - not a password
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"
    """Computed without a threshold to judge against."""
    ERROR = "error"
    """The check could not run (e.g. missing inputs)."""


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(StrEnum):
    OPEN = "open"
    IN_REMEDIATION = "in_remediation"
    CLOSED = "closed"
    RISK_ACCEPTED = "risk_accepted"


# --- Classification -----------------------------------------------------------


class Classification(_Model):
    """Answers to the classification questions and the governance track they imply."""

    is_model_sr26_2: bool = Field(
        description=(
            "True if the asset is a complex quantitative method that applies statistical, "
            "economic or financial theory to turn inputs into quantitative estimates. "
            "Simple arithmetic and deterministic rule-based software are not models."
        )
    )
    ai_category: AICategory
    consequential_decision: bool = Field(
        default=False,
        description="Makes or substantially influences a consequential decision about a consumer.",
    )
    third_party: bool = False
    jurisdictions: list[str] = Field(default_factory=list, examples=[["US", "US-CO"]])
    rationale: NonEmptyStr
    track: GovernanceTrack

    @classmethod
    def from_answers(
        cls,
        *,
        is_model_sr26_2: bool,
        ai_category: AICategory,
        rationale: str,
        consequential_decision: bool = False,
        third_party: bool = False,
        jurisdictions: list[str] | None = None,
    ) -> Classification:
        """Build a classification, deriving the track from the answers."""
        return cls(
            is_model_sr26_2=is_model_sr26_2,
            ai_category=ai_category,
            consequential_decision=consequential_decision,
            third_party=third_party,
            jurisdictions=jurisdictions or [],
            rationale=rationale,
            track=derive_track(is_model_sr26_2=is_model_sr26_2, ai_category=ai_category),
        )

    @model_validator(mode="after")
    def _track_is_consistent(self) -> Classification:
        expected = derive_track(is_model_sr26_2=self.is_model_sr26_2, ai_category=self.ai_category)
        if self.track != expected:
            raise ValueError(
                f"track {self.track.value!r} is inconsistent with the answers; "
                f"expected {expected.value!r}"
            )
        return self


def derive_track(*, is_model_sr26_2: bool, ai_category: AICategory) -> GovernanceTrack:
    """Route an asset to a governance track.

    Generative and agentic AI go to the AI-system track because SR 26-2 places them
    out of its scope, regardless of whether they would otherwise meet the model definition.
    """
    if ai_category in (AICategory.GENERATIVE, AICategory.AGENTIC):
        return GovernanceTrack.AI_SYSTEM
    if is_model_sr26_2:
        return GovernanceTrack.MODEL
    return GovernanceTrack.NON_MODEL


# --- Risk assessment ----------------------------------------------------------


class TierOverride(_Model):
    tier: int = Field(ge=1, le=4)
    justification: NonEmptyStr
    approved_by: NonEmptyStr


class RiskAssessment(_Model):
    profile_id: NonEmptyStr = Field(examples=["fin-us-banking/default"])
    factor_scores: dict[str, int] = Field(description="Score per tiering factor, keyed by id.")
    computed_score: float = Field(ge=0.0, le=1.0)
    computed_tier: int = Field(ge=1, le=4, description="1 is the highest risk; 4 is immaterial.")
    override: TierOverride | None = None
    aggregate_flags: list[str] = Field(default_factory=list)
    assessed_by: NonEmptyStr
    assessed_at: datetime = Field(default_factory=_now)

    @property
    def tier(self) -> int:
        """The effective tier: the override if one was approved, else the computed tier."""
        return self.override.tier if self.override else self.computed_tier


# --- Assets -------------------------------------------------------------------


class AssetRecord(_Model):
    """Anything under governance: a model or an AI system."""

    id: str = Field(default_factory=_new_id)
    name: NonEmptyStr
    owner: NonEmptyStr
    business_uses: list[NonEmptyStr] = Field(min_length=1, examples=[["credit_decision"]])
    description: str = ""
    ai_category: AICategory
    vendor: str | None = None
    status: LifecycleStatus = LifecycleStatus.INTAKE
    depends_on: list[str] = Field(default_factory=list, description="Ids of upstream assets.")
    classification: Classification | None = None
    risk_assessment: RiskAssessment | None = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _category_matches_classification(self) -> AssetRecord:
        if self.classification and self.classification.ai_category != self.ai_category:
            raise ValueError("classification.ai_category must match the asset's ai_category")
        if self.id in self.depends_on:
            raise ValueError("an asset cannot depend on itself")
        return self


# --- Evidence -----------------------------------------------------------------


class EvidenceItem(_Model):
    """Proof that a control was performed. Items form a hash chain per asset."""

    id: str = Field(default_factory=_new_id)
    asset_id: NonEmptyStr
    control_id: NonEmptyStr
    type: EvidenceType
    summary: str = ""
    check_id: str | None = Field(
        default=None, description="For check_result evidence: which check produced it."
    )
    outcome: CheckOutcome | None = Field(
        default=None, description="For check_result evidence: the overall check outcome."
    )
    payload_uri: str | None = Field(default=None, description="Pointer to the stored artifact.")
    payload_sha256: Sha256Hex | None = None
    pack_version: NonEmptyStr = Field(examples=["fin-us-banking@0.1.0"])
    actor: NonEmptyStr
    recorded_at: datetime = Field(default_factory=_now)
    prev_hash: Sha256Hex | None = Field(
        default=None, description="record_hash() of the previous item for this asset."
    )

    def record_hash(self) -> str:
        """Deterministic SHA-256 over the item's canonical JSON form."""
        payload: dict[str, Any] = self.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_chain(items: list[EvidenceItem]) -> list[str]:
    """Check that ``items`` (oldest first) form an unbroken hash chain.

    Returns a list of problems; an empty list means the chain is intact.
    """
    problems: list[str] = []
    previous: EvidenceItem | None = None
    for index, item in enumerate(items):
        if previous is None:
            if item.prev_hash is not None:
                problems.append(f"item {index} ({item.id}): first item must not have prev_hash")
        else:
            if item.asset_id != previous.asset_id:
                problems.append(f"item {index} ({item.id}): belongs to a different asset")
            if item.prev_hash != previous.record_hash():
                problems.append(f"item {index} ({item.id}): prev_hash does not match")
        previous = item
    return problems


# --- Lifecycle events ---------------------------------------------------------


class LifecycleEvent(_Model):
    """A recorded change of lifecycle status."""

    id: str = Field(default_factory=_new_id)
    asset_id: NonEmptyStr
    from_status: LifecycleStatus
    to_status: LifecycleStatus
    actor: NonEmptyStr
    approver: str | None = None
    note: str = ""
    limits: str | None = Field(
        default=None, description="Use limitations, required for approved_with_limits."
    )
    occurred_at: datetime = Field(default_factory=_now)


# --- Findings -----------------------------------------------------------------


class Finding(_Model):
    id: str = Field(default_factory=_new_id)
    asset_id: NonEmptyStr
    control_id: str | None = None
    severity: Severity
    title: NonEmptyStr
    description: str = ""
    owner: NonEmptyStr
    due_date: date | None = None
    status: FindingStatus = FindingStatus.OPEN
    remediation_evidence: list[str] = Field(
        default_factory=list, description="Ids of evidence items supporting closure."
    )
    risk_acceptance_by: str | None = None
    opened_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _closure_rules(self) -> Finding:
        if self.status is FindingStatus.CLOSED and not self.remediation_evidence:
            raise ValueError("a closed finding requires remediation evidence")
        if self.status is FindingStatus.RISK_ACCEPTED and not self.risk_acceptance_by:
            raise ValueError("risk acceptance requires risk_acceptance_by")
        return self
