from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from aimrg.adverse_action import (
    ReasonMap,
    derive_reasons,
    load_reason_map,
    reason_texts,
)
from aimrg.checks import CheckContext, run_check
from aimrg.core.models import CheckOutcome
from aimrg.datasets import GROUPS, SCORE, synthetic_lending_decisions
from aimrg.packs import load_builtin_pack

REASONS = Path(__file__).resolve().parents[1] / "examples" / "credit-scorecard" / "reasons.yaml"


@pytest.fixture(scope="module")
def decisions() -> pd.DataFrame:
    return synthetic_lending_decisions(seed=7)


def _ctx(df: pd.DataFrame, **params: object) -> CheckContext:
    return CheckContext(
        datasets={"decisions": df},
        score=SCORE,
        params=dict(params),
        thresholds=dict(load_builtin_pack("fin-us-banking").thresholds),
    )


# --- synthetic data -------------------------------------------------------------------


def test_synthetic_decisions_shape(decisions: pd.DataFrame) -> None:
    assert {"declined", "approved", "group", *(f"p_{g}" for g in GROUPS)} <= set(decisions)
    probs = decisions[[f"p_{g}" for g in GROUPS]].sum(axis=1)
    assert np.allclose(probs, 1.0, atol=1e-3)
    assert any(c.startswith("contrib_") for c in decisions)
    # Contributions reproduce the score ranking direction: declined applicants push higher.
    contrib = decisions.filter(like="contrib_").sum(axis=1)
    assert contrib[decisions.declined == 1].mean() > contrib[decisions.declined == 0].mean()


# --- fairness ----------------------------------------------------------------------------


def test_air_with_labels_and_proxy(decisions: pd.DataFrame) -> None:
    labels = run_check(
        "fairness.adverse_impact_ratio", _ctx(decisions, group_col="group", control_group="group_a")
    )
    proxy = run_check(
        "fairness.adverse_impact_ratio",
        _ctx(decisions, group_prob_prefix="p_", control_group="group_a"),
    )
    assert labels.outcome is CheckOutcome.WARN and proxy.outcome is CheckOutcome.WARN
    rows = {r["group"]: r for r in labels.tables["groups"]}
    expected = (
        decisions.loc[decisions.group == "group_b", "approved"].mean()
        / decisions.loc[decisions.group == "group_a", "approved"].mean()
    )
    assert rows["group_b"]["adverse_impact_ratio"] == pytest.approx(expected, abs=1e-6)
    assert rows["group_b"]["p_value"] < 0.05
    assert any("proxy" in n for n in proxy.notes)
    assert labels.inputs["membership"] == "labels"


def test_air_hand_computed_and_adverse_col() -> None:
    df = pd.DataFrame(
        {"g": ["a"] * 100 + ["b"] * 100, "declined": [0] * 80 + [1] * 20 + [0] * 60 + [1] * 40}
    )
    result = run_check(
        "fairness.adverse_impact_ratio",
        _ctx(df, group_col="g", control_group="a", adverse_col="declined"),
    )
    (b,) = [r for r in result.tables["groups"] if r["group"] == "b"]
    assert b["adverse_impact_ratio"] == pytest.approx(0.75)
    assert result.outcome is CheckOutcome.FAIL


def test_smd(decisions: pd.DataFrame) -> None:
    result = run_check(
        "fairness.standardized_mean_difference",
        _ctx(decisions, group_col="group", control_group="group_a"),
    )
    assert result.outcome is CheckOutcome.FAIL
    assert result.metrics["max_standardized_mean_difference"] > 0.3  # type: ignore[operator]
    flipped = run_check(
        "fairness.standardized_mean_difference",
        _ctx(decisions, group_col="group", control_group="group_a", higher_is_riskier=False),
    )
    assert flipped.outcome is CheckOutcome.PASS


def test_protected_groups_and_small_groups(decisions: pd.DataFrame) -> None:
    only_c = run_check(
        "fairness.adverse_impact_ratio",
        _ctx(decisions, group_col="group", control_group="group_a", protected_groups="group_c"),
    )
    assert {r["group"] for r in only_c.tables["groups"]} == {"group_a", "group_c"}
    small = run_check(
        "fairness.adverse_impact_ratio",
        _ctx(decisions, group_col="group", control_group="group_a", min_group_size=10_000),
    )
    assert small.outcome is CheckOutcome.INFO
    assert "No comparison group" in small.summary
    assert any("Excluded" in n for n in small.notes)
    small_smd = run_check(
        "fairness.standardized_mean_difference",
        _ctx(decisions, group_col="group", control_group="group_a", min_group_size=10_000),
    )
    assert "No comparison group" in small_smd.summary


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"group_col": "group"}, "control_group"),
        ({"control_group": "group_a"}, "exactly one"),
        (
            {"control_group": "group_a", "group_col": "group", "group_prob_prefix": "p_"},
            "exactly one",
        ),
        ({"control_group": "zzz", "group_col": "group"}, "not found"),
        ({"control_group": "group_a", "group_prob_prefix": "q_"}, "no columns"),
        (
            {"control_group": "group_a", "group_col": "group", "protected_groups": ["x"]},
            "not found",
        ),
        (
            {
                "control_group": "group_a",
                "group_col": "group",
                "favorable_col": "approved",
                "adverse_col": "declined",
            },
            "only one",
        ),
        ({"control_group": "group_a", "group_col": "group", "favorable_col": SCORE}, "binary"),
    ],
)
def test_fairness_parameter_errors(
    decisions: pd.DataFrame, params: dict[str, object], message: str
) -> None:
    result = run_check("fairness.adverse_impact_ratio", _ctx(decisions, **params))
    assert result.outcome is CheckOutcome.ERROR
    assert message in result.summary


def test_air_undefined_when_control_never_favored() -> None:
    df = pd.DataFrame({"g": ["a", "a", "b", "b"], "approved": [0, 0, 1, 0]})
    result = run_check("fairness.adverse_impact_ratio", _ctx(df, group_col="g", control_group="a"))
    assert "undefined" in result.summary


# --- adverse-action reasons ---------------------------------------------------------------


def test_derive_reasons_ranks_and_groups(decisions: pd.DataFrame) -> None:
    reason_map = load_reason_map(REASONS)
    declined = decisions[decisions.declined == 1]
    codes = derive_reasons(declined, reason_map, max_reasons=2)
    assert list(codes.columns) == ["reason_1", "reason_2"]
    row = declined.iloc[0]
    top = max(
        (c for c in declined.columns if c.startswith("contrib_")), key=lambda c: row[c]
    ).removeprefix("contrib_")
    assert codes.iloc[0]["reason_1"] == reason_map.reasons[top].code
    texts = reason_texts(codes.head(1), reason_map)
    assert texts.iloc[0]["reason_1"] == reason_map.reasons[top].text


def test_derive_reasons_merges_shared_codes_and_flags_unmapped() -> None:
    df = pd.DataFrame(
        {"contrib_a": [0.5], "contrib_b": [0.4], "contrib_c": [0.6], "contrib_d": [-1]}
    )
    reason_map = ReasonMap.model_validate(
        {"reasons": {"a": {"code": "X", "text": "t x"}, "b": {"code": "X", "text": "t x"}}}
    )
    codes = derive_reasons(df, reason_map, max_reasons=3)
    assert codes.iloc[0].tolist() == ["X", "UNMAPPED:c", None]  # a+b=0.9 > c=0.6
    with pytest.raises(ValueError, match="no attribution"):
        derive_reasons(df, reason_map, prefix="shap_")


def _write_map(tmp_path: Path, reasons: dict[str, dict[str, str]]) -> Path:
    path = tmp_path / "map.yaml"
    path.write_text(yaml.safe_dump({"reasons": reasons}), encoding="utf-8")
    return path


def test_reason_mapping_check(decisions: pd.DataFrame, tmp_path: Path) -> None:
    ok = run_check("adverse_action.reason_mapping", _ctx(decisions, reason_map=str(REASONS)))
    assert ok.outcome is CheckOutcome.PASS
    assert sum(r["applicants_as_primary_reason"] for r in ok.tables["primary_reasons"]) == int(
        decisions.declined.sum()
    )
    partial = yaml.safe_load(REASONS.read_text())["reasons"]
    partial.pop("utilization")
    partial["ghost_factor"] = {"code": "R99", "text": "A factor the model does not use"}
    missing = run_check(
        "adverse_action.reason_mapping",
        _ctx(decisions, reason_map=str(_write_map(tmp_path, partial))),
    )
    assert missing.outcome is CheckOutcome.FAIL
    assert any("utilization" in n for n in missing.notes)
    assert any("ghost_factor" in n for n in missing.notes)


def test_reason_mapping_without_model_reason(tmp_path: Path) -> None:
    df = pd.DataFrame({"declined": [1, 1], "contrib_a": [0.4, -0.2]})
    path = _write_map(tmp_path, {"a": {"code": "A", "text": "Reason about factor a"}})
    result = run_check("adverse_action.reason_mapping", _ctx(df, reason_map=str(path)))
    assert result.outcome is CheckOutcome.WARN
    assert result.metrics["decisions_without_reason"] == 1


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({}, "reason_map"),
        ({"reason_map": "missing.yaml"}, "not found"),
        ({"reason_map": str(REASONS), "attribution_prefix": "shap_"}, "no attribution"),
        ({"reason_map": str(REASONS), "adverse_col": "nope"}, "missing column"),
    ],
)
def test_reason_mapping_errors(
    decisions: pd.DataFrame, params: dict[str, object], message: str
) -> None:
    result = run_check("adverse_action.reason_mapping", _ctx(decisions, **params))
    assert result.outcome is CheckOutcome.ERROR and message in result.summary


def test_reason_mapping_needs_adverse_rows(decisions: pd.DataFrame, tmp_path: Path) -> None:
    result = run_check(
        "adverse_action.reason_mapping",
        _ctx(decisions.assign(declined=0), reason_map=str(REASONS)),
    )
    assert "no rows" in result.summary
    bad = tmp_path / "bad.yaml"
    bad.write_text("reasons: {}\n", encoding="utf-8")
    invalid = run_check("adverse_action.reason_mapping", _ctx(decisions, reason_map=str(bad)))
    assert "invalid reason map" in invalid.summary


def test_reason_specificity(tmp_path: Path) -> None:
    good = run_check(
        "adverse_action.reason_specificity", _ctx(pd.DataFrame(), reason_map=str(REASONS))
    )
    assert good.outcome is CheckOutcome.PASS
    bad = _write_map(
        tmp_path,
        {
            "a": {"code": "A1", "text": "Does not meet our internal standards"},
            "b": {"code": "B1", "text": "Other"},
            "c": {"code": "C1", "text": "Too low"},
            **{
                f"f{i}": {"code": f"S{i}", "text": "Credit profile weaknesses noted"}
                for i in range(4)
            },
        },
    )
    result = run_check(
        "adverse_action.reason_specificity", _ctx(pd.DataFrame(), reason_map=str(bad))
    )
    assert result.outcome is CheckOutcome.FAIL
    assert result.metrics == {
        "reasons": 7,
        "insufficient": 1,
        "vague": 1,
        "over_shared": 4,
        "very_short": 1,
    }
    shared_only = _write_map(
        tmp_path,
        {f"f{i}": {"code": f"S{i}", "text": "Credit profile weaknesses noted"} for i in range(4)},
    )
    warn = run_check(
        "adverse_action.reason_specificity", _ctx(pd.DataFrame(), reason_map=str(shared_only))
    )
    assert warn.outcome is CheckOutcome.WARN
