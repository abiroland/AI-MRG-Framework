"""Built-in checks: data quality, performance, stability and outcomes analysis.

Every check assumes a **higher score means higher risk** (e.g. probability of default).
Pass ``higher_is_riskier=False`` in check params when the score runs the other way.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from aimrg.checks import metrics
from aimrg.checks.base import (
    CheckContext,
    CheckError,
    CheckResult,
    ThresholdEvaluation,
    check,
    worst,
)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """JSON-safe records (numpy scalars and NaN converted)."""
    parsed: list[dict[str, Any]] = json.loads(df.to_json(orient="records"))
    return parsed


def _oriented(ctx: CheckContext, score: pd.Series) -> pd.Series:
    return score if ctx.params.get("higher_is_riskier", True) else -score


def _round(values: dict[str, float]) -> dict[str, float | int | str | None]:
    return {k: round(v, 6) for k, v in values.items()}


def _outcome(evaluations: list[ThresholdEvaluation]) -> Any:
    return worst([e.outcome for e in evaluations])


# --- data -----------------------------------------------------------------------


@check("data.quality_summary")
def quality_summary(ctx: CheckContext) -> CheckResult:
    """Missingness, constant columns and duplicate rows for every supplied dataset."""
    if not ctx.datasets:
        raise CheckError("needs at least one dataset")
    rows: list[dict[str, Any]] = []
    evaluations: list[ThresholdEvaluation] = []
    notes: list[str] = []
    metrics_out: dict[str, float | int | str | None] = {}
    for role, df in ctx.datasets.items():
        columns = ctx.feature_columns(df)
        if df.empty:
            raise CheckError(f"dataset {role!r} is empty")
        missing = df[columns].isna().mean()
        for column in columns:
            series = df[column]
            numeric = pd.api.types.is_numeric_dtype(series)
            rows.append(
                {
                    "dataset": role,
                    "column": column,
                    "dtype": str(series.dtype),
                    "missing_rate": round(float(missing[column]), 6),
                    "n_unique": int(series.nunique(dropna=True)),
                    "min": float(series.min()) if numeric and series.notna().any() else None,
                    "max": float(series.max()) if numeric and series.notna().any() else None,
                }
            )
            if series.nunique(dropna=True) <= 1:
                notes.append(f"{role}.{column} is constant")
        worst_col = str(missing.idxmax()) if len(missing) else ""
        worst_rate = float(missing.max()) if len(missing) else 0.0
        duplicates = int(df.duplicated().sum())
        metrics_out[f"{role}.rows"] = len(df)
        metrics_out[f"{role}.max_missing_rate"] = round(worst_rate, 6)
        metrics_out[f"{role}.duplicate_rows"] = duplicates
        if duplicates:
            notes.append(f"{role} has {duplicates} duplicate row(s)")
        evaluation = ctx.evaluate("missing_rate", worst_rate)
        if evaluation:
            evaluation.name = f"missing_rate ({role}.{worst_col})"
            evaluations.append(evaluation)
    outcome = _outcome(evaluations)
    return CheckResult(
        check_id="data.quality_summary",
        outcome=outcome,
        summary=(
            f"Profiled {len(ctx.datasets)} dataset(s), {len(rows)} column(s). "
            f"Worst missing rate {max(r['missing_rate'] for r in rows):.1%}."
        ),
        metrics=metrics_out,
        tables={"columns": rows},
        thresholds=evaluations,
        inputs={role: {"rows": len(df)} for role, df in ctx.datasets.items()},
        notes=notes,
    )


@check("data.representativeness")
def representativeness(ctx: CheckContext) -> CheckResult:
    """Characteristic stability (PSI per feature) of the development sample versus the
    population the model is applied to."""
    dev_role, dev = ctx.pick("dev")
    pop_role, pop = ctx.pick("population", "current", "oot")
    columns = ctx.feature_columns(dev)
    ctx.require_columns(pop, pop_role, *columns)
    rows: list[dict[str, Any]] = []
    for column in columns:
        value, _ = metrics.psi(dev[column], pop[column])
        rows.append({"feature": column, "psi": round(value, 6)})
    rows.sort(key=lambda r: r["psi"], reverse=True)
    top = rows[0]
    evaluations = []
    evaluation = ctx.evaluate("population_stability_index", top["psi"])
    if evaluation:
        evaluation.name = f"population_stability_index ({top['feature']})"
        evaluations.append(evaluation)
    return CheckResult(
        check_id="data.representativeness",
        outcome=_outcome(evaluations),
        summary=(
            f"Compared {len(columns)} feature(s) between {dev_role!r} and {pop_role!r}; "
            f"largest shift: {top['feature']} (PSI {top['psi']:.3f})."
        ),
        metrics={"max_feature_psi": top["psi"], "features_compared": len(columns)},
        tables={"feature_psi": rows},
        thresholds=evaluations,
        inputs={dev_role: {"rows": len(dev)}, pop_role: {"rows": len(pop)}},
    )


# --- performance ------------------------------------------------------------------


def _compare_discrimination(
    ctx: CheckContext,
    check_id: str,
    reference_roles: tuple[str, ...],
    comparison_roles: tuple[str, ...],
) -> CheckResult:
    target, score = ctx.need_target(), ctx.need_score()
    ref_role, ref = ctx.pick(*reference_roles)
    cmp_role, cmp_ = ctx.pick(*comparison_roles)
    ctx.require_columns(ref, ref_role, target, score)
    ctx.require_columns(cmp_, cmp_role, target, score)
    try:
        ref_m = metrics.discrimination(ref[target], _oriented(ctx, ref[score]))
        cmp_m = metrics.discrimination(cmp_[target], _oriented(ctx, cmp_[score]))
    except ValueError as exc:
        raise CheckError(str(exc)) from exc
    gini_drop = ref_m["gini"] - cmp_m["gini"]
    evaluations = [e for e in [ctx.evaluate("gini_drop", gini_drop)] if e]
    table = [
        {"dataset": ref_role, **_round(ref_m)},
        {"dataset": cmp_role, **_round(cmp_m)},
    ]
    return CheckResult(
        check_id=check_id,
        outcome=_outcome(evaluations),
        summary=(
            f"Gini {ref_m['gini']:.3f} on {ref_role!r} vs {cmp_m['gini']:.3f} on {cmp_role!r} "
            f"(drop {gini_drop:+.3f}); KS {cmp_m['ks']:.3f} on {cmp_role!r}."
        ),
        metrics={
            "reference_gini": round(ref_m["gini"], 6),
            "comparison_gini": round(cmp_m["gini"], 6),
            "gini_drop": round(gini_drop, 6),
            "comparison_auc": round(cmp_m["auc"], 6),
            "comparison_ks": round(cmp_m["ks"], 6),
        },
        tables={"discrimination": table},
        thresholds=evaluations,
        inputs={ref_role: {"rows": len(ref)}, cmp_role: {"rows": len(cmp_)}},
    )


@check("performance.out_of_sample")
def out_of_sample(ctx: CheckContext) -> CheckResult:
    """Discrimination (AUC, Gini, KS) on the hold-out sample versus development."""
    return _compare_discrimination(ctx, "performance.out_of_sample", ("dev",), ("test",))


@check("performance.out_of_time")
def out_of_time(ctx: CheckContext) -> CheckResult:
    """Discrimination on a later, out-of-time sample versus development."""
    return _compare_discrimination(ctx, "performance.out_of_time", ("dev",), ("oot",))


@check("performance.discrimination_drift")
def discrimination_drift(ctx: CheckContext) -> CheckResult:
    """Discrimination in the monitored period versus the baseline period."""
    return _compare_discrimination(
        ctx, "performance.discrimination_drift", ("baseline", "dev"), ("current",)
    )


# --- stability ----------------------------------------------------------------------


@check("stability.population_stability")
def population_stability(ctx: CheckContext) -> CheckResult:
    """Score PSI between baseline and current periods, plus feature-level CSI."""
    score = ctx.need_score()
    base_role, base = ctx.pick("baseline", "dev")
    cur_role, cur = ctx.pick("current", "oot")
    ctx.require_columns(base, base_role, score)
    ctx.require_columns(cur, cur_role, score)
    score_psi, table = metrics.psi(base[score], cur[score])
    evaluations = [e for e in [ctx.evaluate("population_stability_index", score_psi)] if e]

    csi_rows: list[dict[str, Any]] = []
    for column in ctx.feature_columns(base):
        if column in cur.columns:
            value, _ = metrics.psi(base[column], cur[column])
            csi_rows.append({"feature": column, "csi": round(value, 6)})
    csi_rows.sort(key=lambda r: r["csi"], reverse=True)
    notes = []
    limit = ctx.thresholds.get("population_stability_index")
    if limit and limit.warn is not None:
        shifted = [r["feature"] for r in csi_rows if r["csi"] > limit.warn]
        if shifted:
            notes.append(f"Features with CSI above {limit.warn}: {', '.join(shifted)}")
    return CheckResult(
        check_id="stability.population_stability",
        outcome=_outcome(evaluations),
        summary=f"Score PSI {score_psi:.3f} between {base_role!r} and {cur_role!r}.",
        metrics={
            "score_psi": round(score_psi, 6),
            "max_feature_csi": csi_rows[0]["csi"] if csi_rows else None,
        },
        tables={"score_distribution": _records(table.reset_index()), "feature_csi": csi_rows},
        thresholds=evaluations,
        inputs={base_role: {"rows": len(base)}, cur_role: {"rows": len(cur)}},
        notes=notes,
    )


# --- outcomes -----------------------------------------------------------------------


@check("outcomes.backtest")
def backtest(ctx: CheckContext) -> CheckResult:
    """Compare predicted probabilities with realized outcomes, overall and by band.

    The score must be a probability of the event (between 0 and 1).
    """
    target, score = ctx.need_target(), ctx.need_score()
    role, df = ctx.pick("current", "oot", "test")
    ctx.require_columns(df, role, target, score)
    probs = df[score]
    if probs.min() < 0 or probs.max() > 1:
        raise CheckError("outcomes.backtest needs the score as a probability between 0 and 1")
    expected = float(probs.mean())
    observed = float(df[target].mean())
    relative_error = abs(observed - expected) / max(expected, metrics.EPSILON)
    bands = metrics.calibration_table(df[target], probs, bins=int(ctx.params.get("bins", 10)))
    evaluations = [e for e in [ctx.evaluate("backtest_relative_error", relative_error)] if e]
    return CheckResult(
        check_id="outcomes.backtest",
        outcome=_outcome(evaluations),
        summary=(
            f"Observed event rate {observed:.2%} vs predicted {expected:.2%} on {role!r} "
            f"(relative error {relative_error:.1%})."
        ),
        metrics={
            "observed_rate": round(observed, 6),
            "predicted_rate": round(expected, 6),
            "relative_error": round(relative_error, 6),
            "max_band_difference": round(float(bands["difference"].abs().max()), 6),
        },
        tables={"calibration_by_band": _records(bands)},
        thresholds=evaluations,
        inputs={role: {"rows": len(df)}},
    )
