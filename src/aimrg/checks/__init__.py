"""Automated checks that produce evidence for controls."""

from aimrg.checks import builtin as _builtin  # noqa: F401  (registers built-in checks)
from aimrg.checks.base import (
    CheckContext,
    CheckError,
    CheckResult,
    ThresholdEvaluation,
    available_checks,
    check,
    evaluate_threshold,
    get_check,
    run_check,
    worst,
)
from aimrg.checks.runner import CheckRun, run_control_checks

__all__ = [
    "CheckContext",
    "CheckError",
    "CheckResult",
    "CheckRun",
    "ThresholdEvaluation",
    "available_checks",
    "check",
    "evaluate_threshold",
    "get_check",
    "run_check",
    "run_control_checks",
    "worst",
]
