"""Checks for the AI-system track (generative and agentic AI).

SR 26-2 places generative and agentic AI outside its scope; these checks support the
AI-system track built on NIST AI RMF and its Generative AI Profile (NIST AI 600-1).

AI-MRG does not call models or run evaluations itself. It **assesses the results** of
the evaluations, red-teaming and agent logging your teams already run, so that evidence
is consistent, thresholded and recorded.

Datasets (roles):

``evaluations``
    One row per evaluation case: ``category`` and ``passed`` (1/0). Categories are
    yours to define; the defaults below map to NIST AI 600-1 risks.
``red_team``
    One row per red-team finding: ``severity`` (low/medium/high/critical) and
    ``status`` (open/fixed/accepted). Optional ``category``.
``agent_actions``
    One row per action an agent took: ``tool``, and optionally ``human_approved`` (1/0).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from aimrg.checks.base import (
    CheckContext,
    CheckError,
    CheckResult,
    ThresholdEvaluation,
    check,
    worst,
)
from aimrg.core.models import CheckOutcome

#: Default evaluation categories and the NIST AI 600-1 risk each mainly addresses.
GAI_CATEGORY_RISKS: dict[str, str] = {
    "groundedness": "Confabulation",
    "harmful_content": "Dangerous, Violent, or Hateful Content",
    "privacy_leakage": "Data Privacy",
    "prompt_injection": "Information Security",
    "bias": "Harmful Bias and Homogenization",
    "accuracy": "Confabulation",
}

#: The twelve risks in NIST AI 600-1 (Generative AI Profile), for risk profiles.
NIST_600_1_RISKS: tuple[str, ...] = (
    "CBRN Information or Capabilities",
    "Confabulation",
    "Dangerous, Violent, or Hateful Content",
    "Data Privacy",
    "Environmental Impacts",
    "Harmful Bias and Homogenization",
    "Human-AI Configuration",
    "Information Integrity",
    "Information Security",
    "Intellectual Property",
    "Obscene, Degrading, and/or Abusive Content",
    "Value Chain and Component Integration",
)


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


@check("genai.evaluation_suite")
def evaluation_suite(ctx: CheckContext) -> CheckResult:
    """Pass rate per evaluation category against thresholds, with required categories
    and minimum case counts."""
    role, df = ctx.pick("evaluations", "current", "test")
    category_col = str(ctx.params.get("category_col", "category"))
    passed_col = str(ctx.params.get("passed_col", "passed"))
    ctx.require_columns(df, role, category_col, passed_col)
    if not set(df[passed_col].dropna().unique()) <= {0, 1}:
        raise CheckError(f"{passed_col!r} must be 1 (passed) or 0 (failed)")
    required = _as_list(ctx.params.get("required_categories"))
    min_cases = int(ctx.params.get("min_cases", 30))

    grouped = df.groupby(category_col)[passed_col].agg(["count", "mean"])
    rows: list[dict[str, Any]] = []
    evaluations: list[ThresholdEvaluation] = []
    notes: list[str] = []
    for category, stats in grouped.iterrows():
        name = str(category)
        n, rate = int(stats["count"]), float(stats["mean"])
        rows.append(
            {
                "category": name,
                "nist_600_1_risk": GAI_CATEGORY_RISKS.get(name, "—"),
                "cases": n,
                "pass_rate": round(rate, 6),
            }
        )
        if n < min_cases:
            notes.append(f"{name}: only {n} case(s); at least {min_cases} expected")
        threshold_name = (
            f"genai_pass_rate.{name}"
            if f"genai_pass_rate.{name}" in ctx.thresholds
            else "genai_pass_rate"
        )
        evaluation = ctx.evaluate(threshold_name, rate)
        if evaluation:
            evaluation.name = f"pass rate ({name})"
            evaluations.append(evaluation)
    missing = [c for c in required if c not in set(grouped.index.astype(str))]
    outcomes = [e.outcome for e in evaluations]
    if missing:
        notes.insert(0, f"Required categories with no evaluation cases: {', '.join(missing)}")
        outcomes.append(CheckOutcome.FAIL)
    if any("at least" in n for n in notes) and CheckOutcome.WARN not in outcomes:
        outcomes.append(CheckOutcome.WARN)
    lowest = min(rows, key=lambda r: r["pass_rate"]) if rows else None
    return CheckResult(
        check_id="genai.evaluation_suite",
        outcome=worst(outcomes),
        summary=(
            f"{len(df)} evaluation case(s) in {len(rows)} categor{'y' if len(rows) == 1 else 'ies'}"
            + (
                f"; lowest pass rate {lowest['pass_rate']:.1%} ({lowest['category']})"
                if lowest
                else ""
            )
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + "."
        ),
        metrics={
            "cases": len(df),
            "categories": len(rows),
            "missing_required": len(missing),
            "min_pass_rate": lowest["pass_rate"] if lowest else None,
        },
        tables={"categories": rows},
        thresholds=evaluations,
        inputs={role: {"rows": len(df)}},
        notes=notes,
    )


_SEVERITIES = ("low", "medium", "high", "critical")


@check("genai.red_team_log")
def red_team_log(ctx: CheckContext) -> CheckResult:
    """Red-team findings by severity and status: open high or critical findings fail,
    open medium findings warn."""
    role, df = ctx.pick("red_team", "current")
    ctx.require_columns(df, role, "severity", "status")
    severity = df["severity"].astype("string").str.lower()
    status = df["status"].astype("string").str.lower()
    unknown = sorted(set(severity.dropna()) - set(_SEVERITIES))
    if unknown:
        raise CheckError(f"unknown severities {unknown}; use {list(_SEVERITIES)}")
    if df.empty:
        notes = ["No red-team findings recorded. Confirm red-teaming actually took place."]
        return CheckResult(
            check_id="genai.red_team_log",
            outcome=CheckOutcome.WARN,
            summary="Red-team log is empty.",
            inputs={role: {"rows": 0}},
            notes=notes,
        )
    table = (
        pd.crosstab(severity, status)
        .reindex(index=[s for s in _SEVERITIES if s in set(severity)], fill_value=0)
        .reset_index()
        .rename(columns={"severity": "severity"})
    )
    open_mask = status == "open"
    open_counts = severity[open_mask].value_counts()
    open_high = int(open_counts.get("high", 0) + open_counts.get("critical", 0))
    open_medium = int(open_counts.get("medium", 0))
    if open_high:
        outcome = CheckOutcome.FAIL
    elif open_medium:
        outcome = CheckOutcome.WARN
    else:
        outcome = CheckOutcome.PASS
    records: list[dict[str, Any]] = [
        {str(k): (int(v) if not isinstance(v, str) else v) for k, v in row.items()}
        for row in table.to_dict("records")
    ]
    return CheckResult(
        check_id="genai.red_team_log",
        outcome=outcome,
        summary=(
            f"{len(df)} red-team finding(s); {int(open_mask.sum())} open "
            f"({open_high} high/critical, {open_medium} medium)."
        ),
        metrics={
            "findings": len(df),
            "open": int(open_mask.sum()),
            "open_high_or_critical": open_high,
            "open_medium": open_medium,
        },
        tables={"by_severity_and_status": records},
        inputs={role: {"rows": len(df)}},
    )


@check("genai.agent_permission_audit")
def agent_permission_audit(ctx: CheckContext) -> CheckResult:
    """Agent actions stay within the allowed tools, and tools that need human approval
    were approved before use."""
    role, df = ctx.pick("agent_actions", "current")
    tool_col = str(ctx.params.get("tool_col", "tool"))
    ctx.require_columns(df, role, tool_col)
    allowed = set(_as_list(ctx.params.get("allowed_tools")))
    if not allowed:
        raise CheckError("set params.allowed_tools (comma-separated tool names)")
    needs_approval = set(_as_list(ctx.params.get("human_approval_tools")))
    approved_col = str(ctx.params.get("approved_col", "human_approved"))
    if needs_approval:
        ctx.require_columns(df, role, approved_col)

    tools = df[tool_col].astype("string")
    unauthorized = df[~tools.isin(allowed)]
    unapproved = (
        df[tools.isin(needs_approval) & (df[approved_col].fillna(0) != 1)]
        if needs_approval
        else df.iloc[0:0]
    )
    usage = tools.value_counts()
    rows = [
        {
            "tool": str(tool),
            "actions": int(count),
            "allowed": str(tool) in allowed,
            "needs_human_approval": str(tool) in needs_approval,
            "unapproved_actions": int((unapproved[tool_col].astype("string") == tool).sum()),
        }
        for tool, count in usage.items()
    ]
    unused = sorted(allowed - set(usage.index.astype(str)))
    notes = []
    if unused:
        notes.append(
            f"Allowed but unused tools (consider removing the permission): {', '.join(unused)}"
        )
    outcome = CheckOutcome.FAIL if len(unauthorized) or len(unapproved) else CheckOutcome.PASS
    return CheckResult(
        check_id="genai.agent_permission_audit",
        outcome=outcome,
        summary=(
            f"{len(df)} agent action(s) across {len(usage)} tool(s); "
            f"{len(unauthorized)} outside the allowed tools, "
            f"{len(unapproved)} without required human approval."
        ),
        metrics={
            "actions": len(df),
            "unauthorized_actions": len(unauthorized),
            "unapproved_actions": len(unapproved),
            "unused_permissions": len(unused),
        },
        tables={"tools": rows},
        inputs={role: {"rows": len(df)}},
        notes=notes,
    )


@check("sensitivity.alternative_assumptions")
def alternative_assumptions(ctx: CheckContext) -> CheckResult:
    """Compare the model with alternative specifications scored on the same sample:
    discrimination of each, and how closely their rankings agree with the model's."""
    from aimrg.checks import metrics

    target, score = ctx.need_target(), ctx.need_score()
    role, df = ctx.pick("dev", "test", "oot")
    alternatives = _as_list(ctx.params.get("alternative_scores"))
    if not alternatives:
        raise CheckError(
            "set params.alternative_scores: columns holding scores from alternative "
            "specifications (e.g. different variables, segmentation or assumptions)"
        )
    ctx.require_columns(df, role, target, score, *alternatives)
    try:
        base_gini = metrics.gini(df[target], df[score])
        rows: list[dict[str, Any]] = [
            {"specification": score, "gini": round(base_gini, 6), "rank_correlation": 1.0}
        ]
        for column in alternatives:
            rows.append(
                {
                    "specification": column,
                    "gini": round(metrics.gini(df[target], df[column]), 6),
                    "rank_correlation": round(float(df[score].rank().corr(df[column].rank())), 6),
                }
            )
    except ValueError as exc:
        raise CheckError(str(exc)) from exc
    best = max(rows, key=lambda r: r["gini"])
    notes = []
    if best["specification"] != score:
        notes.append(
            f"Alternative {best['specification']!r} discriminates better "
            f"(Gini {best['gini']:.3f} vs {base_gini:.3f}); document why the chosen "
            "specification was preferred."
        )
    return CheckResult(
        check_id="sensitivity.alternative_assumptions",
        outcome=CheckOutcome.INFO,
        summary=(
            f"Compared {len(alternatives)} alternative specification(s) on {role!r}; "
            f"model Gini {base_gini:.3f}, best {best['specification']} ({best['gini']:.3f})."
        ),
        metrics={"model_gini": round(base_gini, 6), "best_gini": best["gini"]},
        tables={"specifications": rows},
        inputs={role: {"rows": len(df)}},
        notes=notes,
    )
