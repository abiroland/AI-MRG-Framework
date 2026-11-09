"""Checks on adverse-action reasons (ECOA / Regulation B).

Both checks need ``params.reason_map``: the path to a YAML reason map (see
:mod:`aimrg.adverse_action`). The mapping check also needs a decisions dataset with
per-applicant attribution columns (``params.attribution_prefix``, default ``contrib_``)
and an adverse-action flag (``params.adverse_col``, default ``declined``; 1 = adverse).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aimrg.adverse_action import (
    DEFAULT_INSUFFICIENT_PHRASES,
    DEFAULT_VAGUE_EXACT,
    ReasonMap,
    attribution_columns,
    load_reason_map,
    principal_factors,
)
from aimrg.checks.base import CheckContext, CheckError, CheckResult, check
from aimrg.core.models import CheckOutcome


def _reason_map(ctx: CheckContext) -> ReasonMap:
    path = ctx.params.get("reason_map")
    if not path:
        raise CheckError("set params.reason_map to the reason-map YAML file")
    if not Path(str(path)).is_file():
        raise CheckError(f"reason map not found: {path}")
    try:
        return load_reason_map(str(path))
    except (ValidationError, ValueError) as exc:
        raise CheckError(f"invalid reason map {path}: {exc}") from exc


@check("adverse_action.reason_mapping")
def reason_mapping(ctx: CheckContext) -> CheckResult:
    """Every factor that is a principal reason for an adverse action has a reason
    statement, and every adversely treated applicant receives at least one reason."""
    reason_map = _reason_map(ctx)
    role, df = ctx.pick("decisions", "current", "oot", "test", "dev")
    prefix = str(ctx.params.get("attribution_prefix", "contrib_"))
    adverse_col = str(ctx.params.get("adverse_col", "declined"))
    max_reasons = int(ctx.params.get("max_reasons", 4))
    ctx.require_columns(df, role, adverse_col)
    columns = attribution_columns(df, prefix)
    if not columns:
        raise CheckError(f"no attribution columns starting with {prefix!r} in {role!r}")

    adverse = df[df[adverse_col] == 1]
    if adverse.empty:
        raise CheckError(f"no rows with {adverse_col} == 1 in {role!r}")

    unmapped_hits: Counter[str] = Counter()
    primary: Counter[str] = Counter()
    without_reason = 0
    for _, row in adverse.iterrows():
        factors = principal_factors(row, columns, max_reasons)
        if not factors:
            without_reason += 1
            continue
        for factor, _ in factors:
            if factor not in reason_map.reasons:
                unmapped_hits[factor] += 1
        top = factors[0][0]
        primary[
            reason_map.reasons[top].code if top in reason_map.reasons else f"UNMAPPED:{top}"
        ] += 1

    unused = sorted(set(reason_map.reasons) - set(columns))
    notes = []
    if unmapped_hits:
        notes.append(
            "Factors that were principal reasons but have no reason statement: "
            + ", ".join(f"{f} ({n} applicants)" for f, n in unmapped_hits.most_common())
        )
    if without_reason:
        notes.append(
            f"{without_reason} adverse decision(s) had no factor pushing toward the adverse "
            "outcome; reasons must come from another source (e.g. policy rules) and be documented."
        )
    if unused:
        notes.append(f"Reason-map entries for factors not in the attributions: {', '.join(unused)}")

    if unmapped_hits:
        outcome = CheckOutcome.FAIL
    elif without_reason:
        outcome = CheckOutcome.WARN
    else:
        outcome = CheckOutcome.PASS

    by_code = reason_map.by_code()
    table: list[dict[str, Any]] = [
        {
            "reason_code": code,
            "reason": by_code[code].text if code in by_code else "(no reason statement)",
            "applicants_as_primary_reason": n,
            "share": round(n / len(adverse), 6),
        }
        for code, n in primary.most_common()
    ]
    return CheckResult(
        check_id="adverse_action.reason_mapping",
        outcome=outcome,
        summary=(
            f"{len(adverse)} adverse decision(s) on {role!r}; "
            f"{len(unmapped_hits)} principal factor(s) without a reason statement; "
            f"{without_reason} decision(s) without a model-derived reason."
        ),
        metrics={
            "adverse_decisions": len(adverse),
            "factors": len(columns),
            "unmapped_principal_factors": len(unmapped_hits),
            "decisions_without_reason": without_reason,
            "max_reasons": max_reasons,
        },
        tables={"primary_reasons": table},
        inputs={role: {"rows": len(df)}, "reason_map": str(ctx.params.get("reason_map"))},
        notes=notes,
    )


@check("adverse_action.reason_specificity")
def reason_specificity(ctx: CheckContext) -> CheckResult:
    """Reason statements are specific: not internal-policy or qualifying-score
    statements, not vague one-word reasons, and not shared by many different factors."""
    reason_map = _reason_map(ctx)
    phrases = [
        p.lower() for p in ctx.params.get("insufficient_phrases", DEFAULT_INSUFFICIENT_PHRASES)
    ]
    vague = {v.lower() for v in ctx.params.get("vague_reasons", DEFAULT_VAGUE_EXACT)}
    max_shared = int(ctx.params.get("max_factors_per_reason", 3))
    min_words = int(ctx.params.get("min_words", 3))

    factors_by_text: defaultdict[str, list[str]] = defaultdict(list)
    for factor, entry in reason_map.reasons.items():
        factors_by_text[entry.text.strip().lower()].append(factor)

    rows: list[dict[str, Any]] = []
    insufficient = vague_count = shared = short = 0
    for factor, entry in reason_map.reasons.items():
        text = entry.text.strip()
        lowered = text.lower()
        issues = []
        if any(p in lowered for p in phrases):
            issues.append("internal-policy or qualifying-score statement")
            insufficient += 1
        if lowered.rstrip(".") in vague:
            issues.append("vague")
            vague_count += 1
        if len(text.split()) < min_words and lowered.rstrip(".") not in vague:
            issues.append(f"fewer than {min_words} words")
            short += 1
        sharing = factors_by_text[lowered]
        if len(sharing) > max_shared:
            issues.append(f"shared by {len(sharing)} factors")
            shared += 1
        rows.append(
            {"factor": factor, "code": entry.code, "reason": text, "issues": "; ".join(issues)}
        )

    if insufficient or vague_count:
        outcome = CheckOutcome.FAIL
    elif shared or short:
        outcome = CheckOutcome.WARN
    else:
        outcome = CheckOutcome.PASS
    return CheckResult(
        check_id="adverse_action.reason_specificity",
        outcome=outcome,
        summary=(
            f"Reviewed {len(rows)} reason statement(s): {insufficient} insufficient, "
            f"{vague_count} vague, {shared} shared by more than {max_shared} factors, "
            f"{short} very short."
        ),
        metrics={
            "reasons": len(rows),
            "insufficient": insufficient,
            "vague": vague_count,
            "over_shared": shared,
            "very_short": short,
        },
        tables={"reasons": rows},
        inputs={"reason_map": str(ctx.params.get("reason_map"))},
        notes=[
            "Automated screening of wording only. Whether each statement accurately "
            "describes the factor actually scored needs human review."
        ],
    )
