# Example: credit-card underwriting model

An end-to-end walkthrough of AI-MRG on a **simulated** probability-of-default model.
All data are synthetic (`aimrg.datasets.synthetic_credit_portfolio`); no real customer
data are involved.

```bash
python examples/credit-scorecard/run_example.py demo
```

## What happens

1. **Data.** Scored samples are written to `demo/`: `dev.csv` (development), `test.csv`
   (hold-out), `oot.csv` (out-of-time) and `current.csv` (a later monitored period in
   which the population and the model's performance have drifted).
2. **Inventory.** Two assets are registered: a bureau-attribute pipeline and the PD
   model that depends on it.
3. **Classification.** The pipeline is not a model under SR 26-2 (deterministic
   transformations); the PD model is, and it influences consumer credit decisions in
   the US and Colorado.
4. **Tiering.** The PD model scores as Tier 1; the dependency is flagged as aggregate
   risk.
5. **Development testing** (as the developer): data quality, representativeness,
   out-of-sample and out-of-time discrimination. All pass.
6. **Validation** (as the validator): independent out-of-time replication plus
   documents and attestations for the validation and conceptual-soundness controls.
7. **Monitoring.** Score stability is acceptable, but discrimination has dropped and the
   model now over-predicts defaults. Both checks fail and open findings automatically.
8. **Documents.** `validation_report.md`, `validation_report.docx` and
   `inventory_report.md` are generated from the recorded evidence, and the audit trail
   is verified.

## Explore the result with the CLI

```bash
cd demo
aimrg plan "Card Underwriting PD"
aimrg finding list
aimrg evidence list "Card Underwriting PD"
aimrg audit verify
```

## Using your own data

Export scored samples from your model (one row per account, with the score and the
observed outcome) and follow the same steps with `aimrg check run`. See
[Getting started](../../docs/getting-started.md).
