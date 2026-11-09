"""Schemas for content packs: manifest, sources, controls, tiering profiles, thresholds."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from aimrg.core.models import EvidenceType
from aimrg.packs.expr import ExpressionError, compile_expression

ControlId = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,6}-[A-Z]{2,6}-\d{3}$")]
SourceId = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9.\-]*$")]
CheckId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9\-]*$")]
SemVer = Annotated[
    str,
    StringConstraints(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?$"),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControlStage(StrEnum):
    GOVERNANCE = "governance"
    INVENTORY = "inventory"
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    APPROVAL = "approval"
    IMPLEMENTATION = "implementation"
    MONITORING = "monitoring"
    CHANGE = "change"
    RETIREMENT = "retirement"


class Frequency(StrEnum):
    ONCE = "once"
    AT_VALIDATION = "at_validation"
    ON_CHANGE = "on_change"
    CONTINUOUS = "continuous"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"


class Binding(StrEnum):
    LAW = "law"
    REGULATION = "regulation"
    GUIDANCE = "guidance"
    INTERPRETIVE = "interpretive"
    VOLUNTARY = "voluntary"


class Sector(StrEnum):
    FINANCIAL_SERVICES = "financial_services"
    HEALTHCARE = "healthcare"
    CROSS_SECTOR = "cross_sector"


# --- Manifest and sources -----------------------------------------------------


class PackManifest(_Model):
    name: Slug
    version: SemVer
    effective_as_of: date = Field(description="Date the regulatory content reflects.")
    sector: Sector
    title: NonEmptyStr
    description: str = ""
    maintainers: list[NonEmptyStr] = Field(min_length=1)
    reviewed: bool = Field(
        default=False, description="True once practitioner reviewers have signed off."
    )

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class Source(_Model):
    id: SourceId
    title: NonEmptyStr
    issuer: NonEmptyStr
    binding: Binding
    status: NonEmptyStr = Field(examples=["in_force", "effective 2027-01-01"])
    issued: date | None = None
    url: str | None = None
    notes: str = ""


class SourcesFile(_Model):
    sources: list[Source] = Field(min_length=1)


# --- Controls -----------------------------------------------------------------


class Reference(_Model):
    source: SourceId
    section: str | None = None


class CheckSpec(_Model):
    id: CheckId
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)


class Control(_Model):
    id: ControlId
    title: NonEmptyStr
    objective: NonEmptyStr
    lifecycle_stage: ControlStage
    applies_when: NonEmptyStr = Field(
        default="true", description="Applicability expression; see aimrg.packs.expr."
    )
    frequency: Frequency
    evidence_required: list[EvidenceType] = Field(min_length=1)
    checks: list[CheckSpec] = Field(default_factory=list)
    references: list[Reference] = Field(min_length=1)
    guidance: str = Field(default="", description="Practical notes for implementers.")

    @field_validator("applies_when")
    @classmethod
    def _expression_is_valid(cls, value: str) -> str:
        try:
            compile_expression(value)
        except ExpressionError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def _checks_imply_check_evidence(self) -> Control:
        if self.checks and EvidenceType.CHECK_RESULT not in self.evidence_required:
            raise ValueError("controls with automated checks must require check_result evidence")
        return self


class ControlsFile(_Model):
    controls: list[Control] = Field(min_length=1)


# --- Tiering ------------------------------------------------------------------


class TieringFactor(_Model):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    description: NonEmptyStr
    scale_min: int = 1
    scale_max: int = 5
    weight: float = Field(gt=0)
    guide: dict[int, str] = Field(default_factory=dict, description="What each score means.")

    @model_validator(mode="after")
    def _scale(self) -> TieringFactor:
        if self.scale_min >= self.scale_max:
            raise ValueError("scale_min must be less than scale_max")
        bad = [k for k in self.guide if not self.scale_min <= k <= self.scale_max]
        if bad:
            raise ValueError(f"guide keys {bad} are outside the scale")
        return self


class TierCutoff(_Model):
    tier: int = Field(ge=1, le=4)
    min_score: float = Field(ge=0.0, le=1.0)


class TieringProfile(_Model):
    id: Slug
    description: NonEmptyStr
    factors: list[TieringFactor] = Field(min_length=1)
    cutoffs: list[TierCutoff] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def _consistent(self) -> TieringProfile:
        ids = [f.id for f in self.factors]
        if len(ids) != len(set(ids)):
            raise ValueError("factor ids must be unique")
        ordered = sorted(self.cutoffs, key=lambda c: c.tier)
        if [c.tier for c in ordered] != [1, 2, 3, 4]:
            raise ValueError("cutoffs must define tiers 1, 2, 3 and 4 exactly once")
        scores = [c.min_score for c in ordered]
        if any(a <= b for a, b in pairwise(scores)):
            raise ValueError("min_score must strictly decrease from tier 1 to tier 4")
        if scores[-1] != 0.0:
            raise ValueError("tier 4 min_score must be 0.0 so every asset gets a tier")
        return self

    def score(self, factor_scores: dict[str, int]) -> float:
        """Weighted, normalized score in [0, 1]. Every factor must be scored."""
        missing = {f.id for f in self.factors} - factor_scores.keys()
        extra = factor_scores.keys() - {f.id for f in self.factors}
        if missing or extra:
            raise ValueError(f"missing factors {sorted(missing)}; unknown {sorted(extra)}")
        total_weight = sum(f.weight for f in self.factors)
        weighted = 0.0
        for f in self.factors:
            value = factor_scores[f.id]
            if not f.scale_min <= value <= f.scale_max:
                raise ValueError(f"{f.id}={value} is outside {f.scale_min}..{f.scale_max}")
            weighted += f.weight * (value - f.scale_min) / (f.scale_max - f.scale_min)
        return round(weighted / total_weight, 6)

    def tier_for(self, score: float) -> int:
        for cutoff in sorted(self.cutoffs, key=lambda c: c.tier):
            if score >= cutoff.min_score:
                return cutoff.tier
        return 4  # pragma: no cover - tier 4 min_score is always 0.0


# --- Thresholds ---------------------------------------------------------------


class Threshold(_Model):
    warn: float | None = None
    fail: float | None = None
    direction: str = Field(pattern=r"^(above|below)$", description="Which side is bad.")
    note: str = ""

    @model_validator(mode="after")
    def _order(self) -> Threshold:
        if self.warn is None and self.fail is None:
            raise ValueError("set at least one of warn or fail")
        if self.warn is not None and self.fail is not None:
            bad = self.warn > self.fail if self.direction == "above" else self.warn < self.fail
            if bad:
                raise ValueError("warn must be reached before fail")
        return self


class ThresholdsFile(_Model):
    thresholds: dict[str, Threshold] = Field(min_length=1)


# --- Assembled pack -----------------------------------------------------------


class Pack(_Model):
    manifest: PackManifest
    sources: list[Source]
    controls: list[Control]
    tiering_profiles: list[TieringProfile]
    thresholds: dict[str, Threshold] = Field(default_factory=dict)

    def control(self, control_id: str) -> Control:
        for c in self.controls:
            if c.id == control_id:
                return c
        raise KeyError(control_id)

    def tiering_profile(self, profile_id: str) -> TieringProfile:
        for p in self.tiering_profiles:
            if p.id == profile_id:
                return p
        raise KeyError(profile_id)
