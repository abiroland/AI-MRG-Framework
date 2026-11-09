from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest
from conftest import make_model_asset

from aimrg.checks import (
    CheckContext,
    available_checks,
    check,
    evaluate_threshold,
    metrics,
    run_check,
    run_control_checks,
    worst,
)
from aimrg.core.models import CheckOutcome, EvidenceType
from aimrg.datasets import FEATURES, SCORE, TARGET
from aimrg.packs.models import Threshold
from aimrg.registry import Registry, RegistryError

# --- metrics ----------------------------------------------------------------------


def _brute_force_auc(y: list[int], s: list[float]) -> float:
    pos = [b for a, b in zip(y, s, strict=True) if a == 1]
    neg = [b for a, b in zip(y, s, strict=True) if a == 0]
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p, n in itertools.product(pos, neg))
    return wins / (len(pos) * len(neg))


def test_auc_matches_brute_force_with_ties() -> None:
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 200).tolist()
    s = rng.integers(0, 10, 200).astype(float).tolist()  # many ties
    assert metrics.auc(pd.Series(y), pd.Series(s)) == pytest.approx(_brute_force_auc(y, s))
    assert metrics.gini(pd.Series(y), pd.Series(s)) == pytest.approx(2 * _brute_force_auc(y, s) - 1)


def test_ks_and_edge_cases() -> None:
    y = pd.Series([0, 0, 1, 1])
    assert metrics.ks(y, pd.Series([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)
    assert metrics.ks(y, pd.Series([0.5, 0.5, 0.5, 0.5])) == pytest.approx(0.0)
    with pytest.raises(ValueError, match="both classes"):
        metrics.auc(pd.Series([1, 1]), pd.Series([0.1, 0.2]))
    with pytest.raises(ValueError, match="binary"):
        metrics.auc(pd.Series([0, 2]), pd.Series([0.1, 0.2]))


def test_psi_identical_is_zero_and_shift_is_positive() -> None:
    rng = np.random.default_rng(1)
    base = pd.Series(rng.normal(size=5000))
    value, table = metrics.psi(base, base)
    assert value == pytest.approx(0.0, abs=1e-9)
    assert table["expected"].sum() == pytest.approx(1.0)
    shifted, _ = metrics.psi(base, base + 0.5)
    assert shifted > 0.1
    cat, _ = metrics.psi(pd.Series(["a"] * 50 + ["b"] * 50), pd.Series(["a"] * 90 + ["b"] * 10))
    assert cat > 0.3
    constant, _ = metrics.psi(pd.Series([1.0] * 20), pd.Series([1.0] * 20))
    assert constant == pytest.approx(0.0, abs=1e-9)


def test_calibration_table() -> None:
    p = pd.Series(np.linspace(0.01, 0.5, 100))
    y = pd.Series((np.arange(100) % 4 == 0).astype(int))
    table = metrics.calibration_table(y, p, bins=5)
    assert len(table) == 5 and set(table.columns) >= {"predicted", "observed", "difference"}


# --- framework ----------------------------------------------------------------------


def test_threshold_evaluation_and_worst() -> None:
    above = Threshold(warn=0.1, fail=0.25, direction="above")
    assert evaluate_threshold("x", 0.05, above).outcome is CheckOutcome.PASS
    assert evaluate_threshold("x", 0.2, above).outcome is CheckOutcome.WARN
    assert evaluate_threshold("x", 0.3, above).outcome is CheckOutcome.FAIL
    below = Threshold(warn=0.9, fail=0.8, direction="below")
    assert evaluate_threshold("x", 0.85, below).outcome is CheckOutcome.WARN
    assert worst([]) is CheckOutcome.INFO
    assert worst([CheckOutcome.PASS, CheckOutcome.FAIL, CheckOutcome.WARN]) is CheckOutcome.FAIL


def test_registry_of_checks() -> None:
    assert {
        "data.quality_summary",
        "performance.out_of_time",
        "stability.population_stability",
        "outcomes.backtest",
    } <= set(available_checks())
    with pytest.raises(ValueError, match="already registered"):

        @check("data.quality_summary")
        def clash(ctx: CheckContext):  # type: ignore[no-untyped-def]
            raise AssertionError

    missing = run_check("nonexistent.check", CheckContext(datasets={}))
    assert missing.outcome is CheckOutcome.ERROR


def _ctx(datasets: dict[str, pd.DataFrame], **kw: object) -> CheckContext:
    from aimrg.packs import load_builtin_pack

    return CheckContext(
        datasets=datasets,
        target=TARGET,
        score=SCORE,
        features=FEATURES,
        thresholds=dict(load_builtin_pack("fin-us-banking").thresholds),
        **kw,  # type: ignore[arg-type]
    )


# --- built-in checks on the synthetic portfolio ---------------------------------------


def test_validation_checks_pass_on_stable_data(portfolio: dict[str, pd.DataFrame]) -> None:
    for check_id, roles in [
        ("data.quality_summary", ("dev",)),
        ("data.representativeness", ("dev", "oot")),
        ("performance.out_of_sample", ("dev", "test")),
        ("performance.out_of_time", ("dev", "oot")),
    ]:
        datasets = {r: portfolio[r] for r in roles}
        if check_id == "data.representativeness":
            datasets = {"dev": portfolio["dev"], "population": portfolio["oot"]}
        result = run_check(check_id, _ctx(datasets))
        assert result.outcome is CheckOutcome.PASS, (check_id, result.summary)
        assert result.thresholds


def test_monitoring_checks_detect_drift(portfolio: dict[str, pd.DataFrame]) -> None:
    monitored = {"baseline": portfolio["dev"], "current": portfolio["current"]}
    drift = run_check("performance.discrimination_drift", _ctx(monitored))
    assert drift.outcome is CheckOutcome.FAIL
    assert drift.metrics["gini_drop"] > 0.1  # type: ignore[operator]
    backtest = run_check("outcomes.backtest", _ctx({"current": portfolio["current"]}))
    assert backtest.outcome is CheckOutcome.FAIL
    assert backtest.tables["calibration_by_band"]
    stability = run_check("stability.population_stability", _ctx(monitored))
    assert stability.outcome in (CheckOutcome.PASS, CheckOutcome.WARN)
    assert any("utilization" in n for n in stability.notes)


def test_checks_report_missing_inputs(portfolio: dict[str, pd.DataFrame]) -> None:
    no_oot = run_check("performance.out_of_time", _ctx({"dev": portfolio["dev"]}))
    assert no_oot.outcome is CheckOutcome.ERROR and "oot" in no_oot.summary
    no_target = run_check(
        "performance.out_of_time",
        CheckContext(datasets={"dev": portfolio["dev"], "oot": portfolio["oot"]}, score=SCORE),
    )
    assert "target" in no_target.summary
    no_score = run_check(
        "stability.population_stability",
        CheckContext(datasets={"dev": portfolio["dev"], "current": portfolio["current"]}),
    )
    assert "score" in no_score.summary
    bad_col = run_check(
        "performance.out_of_time",
        _ctx({"dev": portfolio["dev"].drop(columns=[TARGET]), "oot": portfolio["oot"]}),
    )
    assert "missing column" in bad_col.summary
    bad_feature = run_check(
        "data.quality_summary",
        _ctx(
            {"dev": portfolio["dev"]},
        ),
    )
    assert bad_feature.outcome is CheckOutcome.PASS
    unknown_feature = run_check(
        "data.quality_summary",
        CheckContext(datasets={"dev": portfolio["dev"]}, features=["nope"]),
    )
    assert "not found" in unknown_feature.summary
    assert (
        run_check("data.quality_summary", CheckContext(datasets={})).outcome is CheckOutcome.ERROR
    )
    empty = run_check(
        "data.quality_summary", CheckContext(datasets={"dev": portfolio["dev"].head(0)})
    )
    assert "empty" in empty.summary
    not_prob = portfolio["current"].assign(**{SCORE: portfolio["current"][SCORE] * 100})
    assert run_check("outcomes.backtest", _ctx({"current": not_prob})).outcome is CheckOutcome.ERROR
    one_class = portfolio["oot"].assign(**{TARGET: 0})
    assert (
        run_check(
            "performance.out_of_time", _ctx({"dev": portfolio["dev"], "oot": one_class})
        ).outcome
        is CheckOutcome.ERROR
    )


def test_score_orientation_param(portfolio: dict[str, pd.DataFrame]) -> None:
    flipped = {r: portfolio[r].assign(**{SCORE: 1 - portfolio[r][SCORE]}) for r in ("dev", "oot")}
    wrong = run_check("performance.out_of_time", _ctx(flipped))
    right = run_check("performance.out_of_time", _ctx(flipped, params={"higher_is_riskier": False}))
    assert wrong.metrics["reference_gini"] < 0 < right.metrics["reference_gini"]  # type: ignore[operator]


def test_quality_notes_constant_and_duplicates() -> None:
    df = pd.DataFrame({"a": [1, 1, 1], "b": [1, 1, 2]})
    result = run_check("data.quality_summary", CheckContext(datasets={"dev": df}))
    assert result.outcome is CheckOutcome.INFO  # no thresholds configured
    assert any("constant" in n for n in result.notes)
    assert any("duplicate" in n for n in result.notes)


# --- runner ---------------------------------------------------------------------------


def test_runner_records_evidence_and_findings(
    registry: Registry, portfolio: dict[str, pd.DataFrame]
) -> None:
    asset = make_model_asset(registry)
    runs = run_control_checks(
        registry,
        asset.id,
        "FIN-MON-001",
        {"baseline": portfolio["dev"], "current": portfolio["current"]},
        actor="validator",
        target=TARGET,
        score=SCORE,
        features=FEATURES,
    )
    by_id = {r.check_id: r for r in runs}
    drift = by_id["performance.discrimination_drift"]
    assert drift.evidence is not None and drift.evidence.outcome is CheckOutcome.FAIL
    assert drift.evidence.check_id == "performance.discrimination_drift"
    assert drift.finding is not None and drift.finding.severity.value == "high"
    stored = registry.read_artifact(drift.evidence.payload_uri or "")
    assert b"discrimination_drift" in stored
    assert registry.verify().ok


def test_runner_skips_unavailable_checks_and_validates_control(
    registry: Registry, portfolio: dict[str, pd.DataFrame]
) -> None:
    asset = make_model_asset(registry)
    runs = run_control_checks(
        registry,
        asset.id,
        "FIN-DEV-003",
        {r: portfolio[r] for r in ("dev", "test", "oot")},
        actor="dev",
        target=TARGET,
        score=SCORE,
        open_findings=False,
    )
    unavailable = [r for r in runs if r.evidence is None]
    assert [r.check_id for r in unavailable] == ["sensitivity.alternative_assumptions"]
    assert len([e for e in registry.evidence(asset.id) if e.type is EvidenceType.CHECK_RESULT]) == 2
    with pytest.raises(RegistryError, match="unknown control"):
        run_control_checks(registry, asset.id, "FIN-ZZZ-001", {}, actor="x")
    with pytest.raises(RegistryError, match="no automated checks"):
        run_control_checks(registry, asset.id, "FIN-VAL-001", {}, actor="x")


def test_control_threshold_override_applies(
    registry: Registry, portfolio: dict[str, pd.DataFrame]
) -> None:
    """FIN-VAL-003 overrides gini_drop; the recorded evaluation should use it."""
    asset = make_model_asset(registry)
    (run,) = run_control_checks(
        registry,
        asset.id,
        "FIN-VAL-003",
        {"dev": portfolio["dev"], "oot": portfolio["oot"]},
        actor="v",
        target=TARGET,
        score=SCORE,
    )
    (evaluation,) = run.result.thresholds
    assert (evaluation.warn, evaluation.fail) == (0.05, 0.10)
