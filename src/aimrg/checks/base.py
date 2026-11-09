"""Check framework: results, context, threshold evaluation and the check registry.

A *check* is a plain function that takes a :class:`CheckContext` and returns a
:class:`CheckResult`. Built-in checks register themselves with :func:`check`; third
parties can add checks through the ``aimrg.checks`` entry-point group, each entry
pointing to a module that registers its checks on import.

Checks work on **scored datasets**: pandas DataFrames that already contain the model's
score and, where relevant, the observed target. That keeps AI-MRG independent of any
modelling framework.

Dataset roles used by the built-in checks:

=========== ===================================================================
role        meaning
=========== ===================================================================
dev         development (training) sample
test        hold-out sample from the development period
oot         out-of-time sample from a later period
baseline    reference period for monitoring (falls back to ``dev``)
current     the period being monitored
population  the population the model is applied to (for representativeness)
=========== ===================================================================
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from aimrg.core.models import CheckOutcome
from aimrg.packs.models import Threshold

_SEVERITY = {
    CheckOutcome.INFO: 0,
    CheckOutcome.PASS: 1,
    CheckOutcome.WARN: 2,
    CheckOutcome.FAIL: 3,
    CheckOutcome.ERROR: 4,
}


class CheckError(Exception):
    """Raised by a check when it cannot run, e.g. a required dataset is missing."""


class ThresholdEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float
    warn: float | None
    fail: float | None
    direction: str
    outcome: CheckOutcome


class CheckResult(BaseModel):
    """The output of one check run. Stored verbatim as a JSON evidence artifact."""

    model_config = ConfigDict(extra="forbid")

    check_id: str
    outcome: CheckOutcome
    summary: str
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    thresholds: list[ThresholdEvaluation] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


@dataclass
class CheckContext:
    datasets: dict[str, pd.DataFrame]
    target: str | None = None
    score: str | None = None
    features: list[str] | None = None
    params: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Threshold] = field(default_factory=dict)

    def pick(self, *roles: str) -> tuple[str, pd.DataFrame]:
        """Return the first dataset present among ``roles``."""
        for role in roles:
            if role in self.datasets:
                return role, self.datasets[role]
        raise CheckError(f"needs one of these datasets: {', '.join(roles)}")

    def require_columns(self, df: pd.DataFrame, role: str, *columns: str | None) -> None:
        missing = [c for c in columns if c is None or c not in df.columns]
        if missing:
            names = ", ".join("<not set>" if c is None else repr(c) for c in missing)
            raise CheckError(f"dataset {role!r} is missing column(s) {names}")

    def need_target(self) -> str:
        if self.target is None:
            raise CheckError("needs the target column (--target)")
        return self.target

    def need_score(self) -> str:
        if self.score is None:
            raise CheckError("needs the score column (--score)")
        return self.score

    def feature_columns(self, df: pd.DataFrame) -> list[str]:
        if self.features:
            missing = [f for f in self.features if f not in df.columns]
            if missing:
                raise CheckError(f"feature column(s) not found: {missing}")
            return list(self.features)
        excluded = {self.target, self.score}
        return [c for c in df.columns if c not in excluded]

    def evaluate(self, name: str, value: float) -> ThresholdEvaluation | None:
        """Judge ``value`` against the named threshold, if one is configured."""
        threshold = self.thresholds.get(name)
        if threshold is None:
            return None
        return evaluate_threshold(name, value, threshold)


def evaluate_threshold(name: str, value: float, threshold: Threshold) -> ThresholdEvaluation:
    def breached(limit: float | None) -> bool:
        if limit is None:
            return False
        return value > limit if threshold.direction == "above" else value < limit

    if breached(threshold.fail):
        outcome = CheckOutcome.FAIL
    elif breached(threshold.warn):
        outcome = CheckOutcome.WARN
    else:
        outcome = CheckOutcome.PASS
    return ThresholdEvaluation(
        name=name,
        value=float(value),
        warn=threshold.warn,
        fail=threshold.fail,
        direction=threshold.direction,
        outcome=outcome,
    )


def worst(outcomes: list[CheckOutcome]) -> CheckOutcome:
    """The most severe outcome; INFO if there is nothing to judge."""
    if not outcomes:
        return CheckOutcome.INFO
    return max(outcomes, key=lambda o: _SEVERITY[o])


CheckFn = Callable[[CheckContext], CheckResult]

_REGISTRY: dict[str, CheckFn] = {}
_entry_points_loaded = False


def check(check_id: str) -> Callable[[CheckFn], CheckFn]:
    """Decorator that registers a check function under ``check_id``."""

    def register(fn: CheckFn) -> CheckFn:
        if check_id in _REGISTRY and _REGISTRY[check_id] is not fn:
            raise ValueError(f"check {check_id!r} is already registered")
        _REGISTRY[check_id] = fn
        return fn

    return register


def _load_plugins() -> None:
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    for ep in entry_points(group="aimrg.checks"):
        ep.load()


def available_checks() -> list[str]:
    _load_plugins()
    return sorted(_REGISTRY)


def get_check(check_id: str) -> CheckFn | None:
    _load_plugins()
    return _REGISTRY.get(check_id)


def run_check(check_id: str, context: CheckContext) -> CheckResult:
    """Run a check, converting expected failures into an ERROR result."""
    fn = get_check(check_id)
    if fn is None:
        return CheckResult(
            check_id=check_id,
            outcome=CheckOutcome.ERROR,
            summary="Not available in this version of AI-MRG.",
        )
    try:
        return fn(context)
    except CheckError as exc:
        return CheckResult(check_id=check_id, outcome=CheckOutcome.ERROR, summary=str(exc))
