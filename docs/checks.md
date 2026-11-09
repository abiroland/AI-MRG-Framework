# Checks

Checks are automated tests that produce `check_result` evidence for a control. A control
lists its checks in the pack; `aimrg check run ASSET CONTROL` runs all of them.

## Inputs: scored datasets and roles

Checks never call your model. You supply **scored datasets**: tables containing the
model's score (`--score`) and, for performance and outcome checks, the observed outcome
(`--target`, 1 = event such as default). This keeps AI-MRG independent of any modelling
framework. Scores are assumed to rise with risk; pass `higher_is_riskier: false` in a
control's check `params` if yours run the other way.

| Role | Meaning |
|---|---|
| `dev` | development (training) sample |
| `test` | hold-out sample from the development period |
| `oot` | out-of-time sample from a later period |
| `baseline` | reference period for monitoring (falls back to `dev`) |
| `current` | the period being monitored |
| `population` | the population the model is applied to |

## Built-in checks

| Check | Uses | Threshold (pack default) | What it reports |
|---|---|---|---|
| `data.quality_summary` | every dataset given | `missing_rate` | missingness, ranges, constant columns, duplicate rows |
| `data.representativeness` | `dev` vs `population` (or `current`/`oot`) | `population_stability_index` on the most-shifted feature | PSI per feature |
| `performance.out_of_sample` | `dev` vs `test` | `gini_drop` | AUC, Gini, KS on each sample |
| `performance.out_of_time` | `dev` vs `oot` | `gini_drop` | AUC, Gini, KS on each sample |
| `performance.discrimination_drift` | `baseline` vs `current` | `gini_drop` | AUC, Gini, KS on each period |
| `stability.population_stability` | `baseline` vs `current` | `population_stability_index` on the score | score PSI by band, feature CSI |
| `outcomes.backtest` | `current` (or `oot`/`test`) | `backtest_relative_error` | observed vs predicted event rate, overall and by band |

Run `aimrg check list` to see what your installation provides, and
`aimrg pack validate fin-us-banking` to see which checks the pack references that are not
implemented yet (GenAI checks arrive in Phase 3).

## Fair-lending checks

| Check | Threshold (pack default) | What it reports |
|---|---|---|
| `fairness.adverse_impact_ratio` | `adverse_impact_ratio` (bad when below) | favorable-outcome rate per group relative to the control group, effective n, two-proportion p-value |
| `fairness.standardized_mean_difference` | `standardized_mean_difference` (bad when above) | mean score per group and the difference from the control group in pooled standard deviations |

They read a decisions dataset (role `decisions`, or `current`/`oot`/`test`/`dev`) and need
these parameters, passed with `--param`:

| Parameter | Meaning |
|---|---|
| `control_group` | the reference group (required) |
| `group_col` | a column of group labels, **or** |
| `group_prob_prefix` | a prefix for proxy-probability columns, e.g. `p_` for `p_group_a`, `p_group_b`; rates are then probability-weighted |
| `protected_groups` | optional; only compare these groups |
| `favorable_col` / `adverse_col` | decision column (default `approved`, 1 = favorable); or give the adverse flag instead |
| `min_group_size` | groups with a smaller effective size are reported but not judged (default 30) |

```bash
aimrg check run "Card Underwriting PD" FIN-CPR-002 --data decisions=decisions.csv \
  --score pd_score --param group_prob_prefix=p_ --param control_group=group_a
```

AI-MRG does **not** ship a proxy method or census data. Supply membership probabilities
from the method your institution uses (for example BISG) and document its limitations.
These are screening metrics: they neither establish nor rule out discrimination, and the
default thresholds are conventions, not legal standards.

## Adverse-action checks

Both read a **reason map**: a YAML file mapping each model factor to a reason code and
statement (see `examples/credit-scorecard/reasons.yaml`).

| Check | Fails when | Warns when |
|---|---|---|
| `adverse_action.reason_mapping` | a factor that is a principal reason for some adverse decision has no reason statement | an adverse decision has no factor pushing toward it (reasons must come from elsewhere) |
| `adverse_action.reason_specificity` | a statement refers to internal standards/policies or a qualifying score, or is a vague one-word reason | several factors share one statement, or a statement is very short |

The mapping check needs a decisions dataset with an adverse flag (`adverse_col`, default
`declined`) and **per-applicant attributions**: one column per factor, prefixed
`contrib_` by default, positive when the factor pushed toward the adverse outcome.
SHAP values work, as does `coefficient × (value − reference)` for a logistic model.

```bash
aimrg check run "Card Underwriting PD" FIN-CPR-001 --data decisions=decisions.csv \
  --param reason_map=reasons.yaml
```

To produce the reasons themselves:

```bash
aimrg reasons derive decisions.csv --map reasons.yaml --max-reasons 4 -o reasons.csv
aimrg reasons derive decisions.csv --map reasons.yaml --text -o reason_statements.csv
```

Factors that share a reason code are combined before ranking, and factors without a
statement appear as `UNMAPPED:<factor>` rather than being dropped. Regulation B does not
cap the number of reasons, but its commentary notes that more than four is unlikely to
help the applicant; `--max-reasons` defaults to 4. Wording checks are automated screening
only; compliance must review whether each statement accurately describes the factor.

## Outcomes

| Outcome | Meaning | Recorded? |
|---|---|---|
| `pass` | within thresholds | yes |
| `warn` | past the warn level | yes |
| `fail` | past the fail level; opens a finding | yes |
| `info` | computed, but no threshold configured | yes |
| `error` | could not run (missing data, column or check) | no |

Thresholds come from the pack's `thresholds/` files and can be overridden per control
(see [Writing packs](packs.md)). The defaults are industry conventions or illustrative
starting points: **calibrate them and record your rationale**.

## Adding your own checks

A check is a function that takes a `CheckContext` and returns a `CheckResult`:

```python
from aimrg.checks import CheckContext, CheckResult, check
from aimrg.core.models import CheckOutcome


@check("mybank.override_rate")
def override_rate(ctx: CheckContext) -> CheckResult:
    """Share of decisions where underwriters overrode the model."""
    role, df = ctx.pick("current")
    rate = float(df["overridden"].mean())
    evaluation = ctx.evaluate("override_rate", rate)
    return CheckResult(
        check_id="mybank.override_rate",
        outcome=evaluation.outcome if evaluation else CheckOutcome.INFO,
        summary=f"Override rate {rate:.1%} on {role!r}.",
        metrics={"override_rate": rate},
        thresholds=[evaluation] if evaluation else [],
    )
```

Register it from your own package with an entry point, so AI-MRG finds it automatically:

```toml
[project.entry-points."aimrg.checks"]
mybank = "mybank_checks"      # a module that defines checks with @check
```

Then reference `mybank.override_rate` from a control in your own pack.
