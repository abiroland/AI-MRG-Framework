"""Risk tiering: score an asset against a tiering profile and record the result."""

from __future__ import annotations

from aimrg.core.models import AssetRecord, RiskAssessment, TierOverride
from aimrg.packs.models import TieringProfile
from aimrg.registry.store import Registry


def assess(
    profile: TieringProfile,
    factor_scores: dict[str, int],
    *,
    profile_ref: str,
    assessed_by: str,
    override: TierOverride | None = None,
    aggregate_flags: list[str] | None = None,
) -> RiskAssessment:
    """Compute a risk assessment. ``profile_ref`` is recorded, e.g. ``fin-us-banking/default``."""
    score = profile.score(factor_scores)
    return RiskAssessment(
        profile_id=profile_ref,
        factor_scores=dict(factor_scores),
        computed_score=score,
        computed_tier=profile.tier_for(score),
        override=override,
        aggregate_flags=aggregate_flags or [],
        assessed_by=assessed_by,
    )


def aggregate_flags(registry: Registry, asset: AssetRecord) -> list[str]:
    """Flags for aggregate model risk from shared inputs and downstream use.

    SR 26-2 asks institutions to consider interactions and dependencies across models.
    """
    flags: list[str] = []
    for upstream_id in asset.depends_on:
        upstream = registry.get(upstream_id)
        siblings = [a for a in registry.dependents(upstream_id) if a.id != asset.id]
        if siblings:
            flags.append(
                f"shares upstream asset {upstream.name!r} with {len(siblings)} other asset(s)"
            )
    downstream = registry.dependents(asset.id)
    if downstream:
        names = ", ".join(sorted(a.name for a in downstream))
        flags.append(f"feeds {len(downstream)} downstream asset(s): {names}")
    return flags


def assess_asset(
    registry: Registry,
    ref: str,
    factor_scores: dict[str, int],
    *,
    assessed_by: str,
    profile_id: str = "default",
    override: TierOverride | None = None,
) -> AssetRecord:
    """Assess an asset with the registry's pack and save the result."""
    asset = registry.get(ref)
    profile = registry.pack.tiering_profile(profile_id)
    assessment = assess(
        profile,
        factor_scores,
        profile_ref=f"{registry.pack.manifest.name}/{profile.id}",
        assessed_by=assessed_by,
        override=override,
        aggregate_flags=aggregate_flags(registry, asset),
    )
    return registry.set_risk_assessment(asset.id, assessment)
