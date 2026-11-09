"""Adverse-action reasons: map model factors to reason statements and derive them.

ECOA / Regulation B require a statement of the *specific* principal reasons for adverse
action, and those reasons must relate to and accurately describe the factors actually
considered or scored (12 CFR 1002.9(b)(2) and its official interpretation; CFPB Circulars
2022-03 and 2023-03). Regulation B states that reasons based on the creditor's internal
standards or policies, or on failing to achieve a qualifying score, are insufficient.

This module works from **per-applicant attributions**: one column per model factor
giving how much that factor pushed the applicant toward the adverse outcome (for
example SHAP values, or for a logistic model ``coefficient * (value - reference)``).
Positive values push toward the adverse action.

AI-MRG does not decide which attribution method is appropriate; that choice and its
justification belong in the model's documentation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

UNMAPPED_PREFIX = "UNMAPPED:"

#: Regulation B says reasons based on internal standards/policies or on failing to reach
#: a qualifying score are insufficient. Phrases that typically signal such statements.
DEFAULT_INSUFFICIENT_PHRASES: tuple[str, ...] = (
    "internal standard",
    "internal polic",
    "qualifying score",
    "credit scoring system",
    "does not meet our",
    "did not meet our",
    "not meet credit standards",
    "insufficient score",
    "score too low",
)
DEFAULT_VAGUE_EXACT: tuple[str, ...] = ("other", "credit", "credit history", "score")


class ReasonEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyStr
    text: NonEmptyStr


class ReasonMap(BaseModel):
    """Model factor -> reason statement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reasons: dict[str, ReasonEntry] = Field(min_length=1)

    def by_code(self) -> dict[str, ReasonEntry]:
        return {entry.code: entry for entry in self.reasons.values()}


def load_reason_map(path: str | Path) -> ReasonMap:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ReasonMap.model_validate(data)


def attribution_columns(df: pd.DataFrame, prefix: str) -> dict[str, str]:
    """Map factor name -> attribution column for columns starting with ``prefix``."""
    return {c.removeprefix(prefix): c for c in df.columns if c.startswith(prefix)}


def principal_factors(
    row: pd.Series, columns: dict[str, str], max_factors: int, min_contribution: float = 0.0
) -> list[tuple[str, float]]:
    """The factors that pushed this applicant most toward the adverse action."""
    contributions = [
        (factor, float(row[column]))
        for factor, column in columns.items()
        if pd.notna(row[column]) and float(row[column]) > min_contribution
    ]
    contributions.sort(key=lambda item: item[1], reverse=True)
    return contributions[:max_factors]


def derive_reasons(
    df: pd.DataFrame,
    reason_map: ReasonMap,
    *,
    prefix: str = "contrib_",
    max_reasons: int = 4,
    min_contribution: float = 0.0,
) -> pd.DataFrame:
    """Derive up to ``max_reasons`` reason codes per row, strongest first.

    Contributions of factors that share a reason code are summed before ranking, so a
    reason is listed once. Factors without a mapping are returned as
    ``UNMAPPED:<factor>`` so they are never silently dropped.
    """
    columns = attribution_columns(df, prefix)
    if not columns:
        raise ValueError(f"no attribution columns starting with {prefix!r}")
    out_rows = []
    for _, row in df.iterrows():
        totals: dict[str, float] = {}
        for factor, value in principal_factors(row, columns, len(columns), min_contribution):
            entry = reason_map.reasons.get(factor)
            code = entry.code if entry else f"{UNMAPPED_PREFIX}{factor}"
            totals[code] = totals.get(code, 0.0) + value
        ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:max_reasons]
        record: dict[str, str | None] = {}
        for i in range(max_reasons):
            record[f"reason_{i + 1}"] = ranked[i][0] if i < len(ranked) else None
        out_rows.append(record)
    return pd.DataFrame(out_rows, index=df.index)


def reason_texts(codes: pd.DataFrame, reason_map: ReasonMap) -> pd.DataFrame:
    """Replace reason codes with their statements (unmapped codes are kept as-is)."""
    lookup = {code: entry.text for code, entry in reason_map.by_code().items()}
    result: pd.DataFrame = codes.apply(lambda col: col.map(lambda c: lookup.get(c, c) if c else c))
    return result
