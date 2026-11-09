"""Fair-lending screening checks: adverse impact ratio and standardized mean difference.

Group membership can be supplied two ways (set in the control's ``params`` or with
``aimrg check run --param``):

* ``group_col``: a column of group labels (e.g. self-reported data where lawfully held),
* ``group_prob_prefix``: probability columns such as ``p_group_a``, ``p_group_b`` from a
  proxy method (e.g. BISG). Rates are then probability-weighted.

``control_group`` names the reference group. ``protected_groups`` optionally restricts
which groups are compared (default: every other group).

These are **screening** metrics. They do not establish or rule out discrimination;
results need statistical and legal review. AI-MRG does not ship a proxy method or census
data; supply proxy probabilities from the method your institution has chosen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from aimrg.checks.base import (
    CheckContext,
    CheckError,
    CheckResult,
    ThresholdEvaluation,
    check,
    worst,
)

DECISION_ROLES = ("decisions", "current", "oot", "test", "dev")


@dataclass(frozen=True)
class GroupWeights:
    weights: dict[str, pd.Series]
    control: str
    probabilistic: bool


def group_weights(ctx: CheckContext, df: pd.DataFrame, role: str) -> GroupWeights:
    """Membership weights per group: 0/1 indicators, or proxy probabilities."""
    control = ctx.params.get("control_group")
    if not control:
        raise CheckError("set params.control_group (the reference group)")
    group_col = ctx.params.get("group_col")
    prefix = ctx.params.get("group_prob_prefix")
    if bool(group_col) == bool(prefix):
        raise CheckError("set exactly one of params.group_col or params.group_prob_prefix")

    weights: dict[str, pd.Series]
    if group_col:
        ctx.require_columns(df, role, str(group_col))
        labels = df[str(group_col)].astype("string")
        weights = {str(g): (labels == g).astype(float) for g in sorted(labels.dropna().unique())}
        probabilistic = False
    else:
        columns = [c for c in df.columns if c.startswith(str(prefix))]
        if not columns:
            raise CheckError(f"no columns starting with {prefix!r} in dataset {role!r}")
        weights = {c.removeprefix(str(prefix)): df[c].fillna(0.0).astype(float) for c in columns}
        probabilistic = True

    if str(control) not in weights:
        raise CheckError(f"control group {control!r} not found; groups: {sorted(weights)}")
    wanted = ctx.params.get("protected_groups")
    if wanted:
        names = [wanted] if isinstance(wanted, str) else list(wanted)
        missing = [g for g in names if g not in weights]
        if missing:
            raise CheckError(f"protected group(s) {missing} not found; groups: {sorted(weights)}")
        weights = {g: w for g, w in weights.items() if g in names or g == control}
    return GroupWeights(weights=weights, control=str(control), probabilistic=probabilistic)


def _two_proportion_p_value(p1: float, n1: float, p2: float, n2: float) -> float | None:
    if n1 <= 0 or n2 <= 0:
        return None
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    variance = pooled * (1 - pooled) * (1 / n1 + 1 / n2)
    if variance <= 0:
        return None
    z = (p1 - p2) / math.sqrt(variance)
    return math.erfc(abs(z) / math.sqrt(2))


def _favorable(ctx: CheckContext, df: pd.DataFrame, role: str) -> pd.Series:
    favorable_col = ctx.params.get("favorable_col")
    adverse_col = ctx.params.get("adverse_col")
    if favorable_col and adverse_col:
        raise CheckError("set only one of params.favorable_col or params.adverse_col")
    if adverse_col:
        ctx.require_columns(df, role, str(adverse_col))
        series = 1 - df[str(adverse_col)]
    else:
        column = str(favorable_col or "approved")
        ctx.require_columns(df, role, column)
        series = df[column]
    if not set(series.dropna().unique()) <= {0, 1}:
        raise CheckError("the decision column must be binary (1/0)")
    return series.astype(float)


def _common_notes(gw: GroupWeights, small: list[str], min_size: float) -> list[str]:
    notes = []
    if gw.probabilistic:
        notes.append(
            "Group membership is probabilistic (proxy); counts are effective sample sizes "
            "and p-values are approximate."
        )
    if small:
        notes.append(
            f"Excluded from thresholds (effective size below {min_size:g}): {', '.join(small)}"
        )
    notes.append("Screening metric only; results require statistical and legal review.")
    return notes


@check("fairness.adverse_impact_ratio")
def adverse_impact_ratio(ctx: CheckContext) -> CheckResult:
    """Favorable-outcome rate of each group relative to the control group."""
    role, df = ctx.pick(*DECISION_ROLES)
    gw = group_weights(ctx, df, role)
    favorable = _favorable(ctx, df, role)
    min_size = float(ctx.params.get("min_group_size", 30))

    def rate(weight: pd.Series) -> tuple[float, float]:
        n = float(weight.sum())
        return (float((weight * favorable).sum()) / n if n > 0 else float("nan")), n

    control_rate, control_n = rate(gw.weights[gw.control])
    if not control_rate > 0:
        raise CheckError("control group has no favorable outcomes; AIR is undefined")
    rows: list[dict[str, Any]] = []
    evaluations: list[ThresholdEvaluation] = []
    small: list[str] = []
    for group, weight in gw.weights.items():
        group_rate, n = rate(weight)
        is_control = group == gw.control
        air = group_rate / control_rate
        p_value = (
            None if is_control else _two_proportion_p_value(group_rate, n, control_rate, control_n)
        )
        rows.append(
            {
                "group": group,
                "role": "control" if is_control else "comparison",
                "effective_n": round(n, 1),
                "favorable_rate": round(group_rate, 6),
                "adverse_impact_ratio": round(air, 6),
                "p_value": None if p_value is None else round(p_value, 6),
            }
        )
        if is_control:
            continue
        if n < min_size:
            small.append(group)
            continue
        evaluation = ctx.evaluate("adverse_impact_ratio", air)
        if evaluation:
            evaluation.name = f"adverse_impact_ratio ({group})"
            evaluations.append(evaluation)
    compared = [r for r in rows if r["role"] == "comparison" and r["group"] not in small]
    lowest = min(compared, key=lambda r: r["adverse_impact_ratio"]) if compared else None
    summary = (
        f"Lowest AIR {lowest['adverse_impact_ratio']:.3f} ({lowest['group']}) vs control "
        f"{gw.control!r} on {role!r}."
        if lowest
        else f"No comparison group large enough on {role!r}."
    )
    return CheckResult(
        check_id="fairness.adverse_impact_ratio",
        outcome=worst([e.outcome for e in evaluations]),
        summary=summary,
        metrics={
            "control_group": gw.control,
            "control_favorable_rate": round(control_rate, 6),
            "min_adverse_impact_ratio": lowest["adverse_impact_ratio"] if lowest else None,
            "groups_compared": len(compared),
        },
        tables={"groups": rows},
        thresholds=evaluations,
        inputs={role: {"rows": len(df)}, "membership": "proxy" if gw.probabilistic else "labels"},
        notes=_common_notes(gw, small, min_size),
    )


def _weighted_mean_var(values: np.ndarray, weights: np.ndarray) -> tuple[float, float, float]:
    total = float(weights.sum())
    if total <= 0:
        return float("nan"), float("nan"), 0.0
    mean = float((weights * values).sum() / total)
    var = float((weights * (values - mean) ** 2).sum() / total)
    return mean, var, total


@check("fairness.standardized_mean_difference")
def standardized_mean_difference(ctx: CheckContext) -> CheckResult:
    """Difference in mean model score between each group and the control group, in
    pooled standard deviations (positive = group scored as riskier)."""
    score = ctx.need_score()
    role, df = ctx.pick(*DECISION_ROLES)
    ctx.require_columns(df, role, score)
    gw = group_weights(ctx, df, role)
    min_size = float(ctx.params.get("min_group_size", 30))
    sign = 1.0 if ctx.params.get("higher_is_riskier", True) else -1.0
    mask = df[score].notna().to_numpy()
    values = df[score].to_numpy(dtype=float)[mask]

    c_mean, c_var, _ = _weighted_mean_var(values, gw.weights[gw.control].to_numpy()[mask])
    rows: list[dict[str, Any]] = []
    evaluations: list[ThresholdEvaluation] = []
    small: list[str] = []
    for group, weight in gw.weights.items():
        mean, var, n = _weighted_mean_var(values, weight.to_numpy()[mask])
        pooled = math.sqrt((var + c_var) / 2) if (var + c_var) > 0 else float("nan")
        smd = sign * (mean - c_mean) / pooled if pooled and not math.isnan(pooled) else 0.0
        is_control = group == gw.control
        rows.append(
            {
                "group": group,
                "role": "control" if is_control else "comparison",
                "effective_n": round(n, 1),
                "mean_score": round(mean, 6),
                "standardized_mean_difference": round(smd, 6),
            }
        )
        if is_control:
            continue
        if n < min_size:
            small.append(group)
            continue
        evaluation = ctx.evaluate("standardized_mean_difference", smd)
        if evaluation:
            evaluation.name = f"standardized_mean_difference ({group})"
            evaluations.append(evaluation)
    compared = [r for r in rows if r["role"] == "comparison" and r["group"] not in small]
    largest = max(compared, key=lambda r: r["standardized_mean_difference"]) if compared else None
    summary = (
        f"Largest SMD {largest['standardized_mean_difference']:+.3f} ({largest['group']}) vs "
        f"control {gw.control!r} on {role!r}."
        if largest
        else f"No comparison group large enough on {role!r}."
    )
    return CheckResult(
        check_id="fairness.standardized_mean_difference",
        outcome=worst([e.outcome for e in evaluations]),
        summary=summary,
        metrics={
            "control_group": gw.control,
            "max_standardized_mean_difference": (
                largest["standardized_mean_difference"] if largest else None
            ),
            "groups_compared": len(compared),
        },
        tables={"groups": rows},
        thresholds=evaluations,
        inputs={role: {"rows": len(df)}, "membership": "proxy" if gw.probabilistic else "labels"},
        notes=_common_notes(gw, small, min_size),
    )
