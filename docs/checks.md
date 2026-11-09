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
implemented yet (fair-lending, adverse-action and GenAI checks arrive in later phases).

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
