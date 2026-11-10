# Example: credit-card underwriting model

An end-to-end walkthrough of AI-MRG on a **simulated** probability-of-default model.
All data are synthetic (`aimrg.datasets.synthetic_credit_portfolio`); no real customer
data are involved.

```bash
python examples/credit-scorecard/run_example.py demo
```

## What happens

1. **Data.** Scored samples are written to `demo/` (`dev`, `test`, `oot`, `current`), plus
   `decisions.csv`: application decisions with per-factor attributions (`contrib_*`),
   synthetic group labels and proxy membership probabilities (`p_group_*`). The groups are
   synthetic stand-ins, not real demographic categories.
2. **Inventory, classification, tiering.** A bureau-attribute pipeline (not a model under
   SR 26-2) and the PD model (Tier 1, consumer credit decisions in the US and Colorado).
3. **Development.** The developer records data-quality and performance testing and the
   model moves to validation.
4. **Validation.** The validator replicates out-of-time performance and runs the
   adverse-action checks against `reasons.yaml`. The fair-lending screen finds a
   standardized mean difference above the illustrative threshold and opens a finding,
   which **blocks approval**.
5. **Resolution and approval.** The fair-lending team records its review; the finding is
   closed with that evidence; an independent approver approves the model.
6. **Production.** Adverse-action notice and Colorado disclosure evidence is recorded and
   the model goes into production.
7. **Monitoring.** Discrimination and calibration have deteriorated: two checks fail, two
   findings open, and a change request is raised.
8. **An agentic AI assistant** on the AI-system track: evaluation results (prompt-injection
   resistance below threshold), red-team findings and an agent action log (one refund
   issued without human approval) are assessed and open findings.
9. **Documents.** Validation (Markdown and Word), fair-lending, adverse-action, Colorado
   notice drafts, AI-system record and inventory reports, plus `examination-package.zip`
   with a SHA-256 manifest (`aimrg export verify examination-package.zip`).

## Explore the result with the CLI

```bash
cd demo
aimrg status "Card Underwriting PD"
aimrg plan "Card Underwriting PD"
aimrg finding aging
aimrg evidence list "Card Underwriting PD"
aimrg audit verify
```

## Using your own data

Export scored samples from your model (one row per account, with the score and the
observed outcome) and follow the same steps with `aimrg check run`. See
[Getting started](../../docs/getting-started.md).
