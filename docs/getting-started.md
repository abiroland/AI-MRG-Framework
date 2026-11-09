# Getting started

## Install

AI-MRG is not on PyPI yet. Install from source:

```bash
git clone https://github.com/abiroland/ai-mrg-framework
cd ai-mrg-framework
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # or ".[docx]" for just Word output
```

Requires Python 3.11 or later.

## See it work in one command

```bash
python examples/credit-scorecard/run_example.py demo
```

This takes a **simulated** credit-card underwriting model through its whole lifecycle:
development testing, validation, fair-lending and adverse-action review (a fair-lending
screen fails, opens a finding, and is resolved with a documented review), independent
approval, production, and a monitoring cycle that detects drift and raises a change
request. It writes validation, fair-lending, adverse-action and inventory reports into
`demo/`.

## The workflow, step by step

Every command takes `--db` (default `aimrg.db`, or set `AIMRG_DB`) and `--actor`, which is
recorded on evidence (defaults to your login, or set `AIMRG_ACTOR`).

### 1. Create a registry

```bash
aimrg init                       # uses the built-in fin-us-banking pack
```

### 2. Register assets

```bash
aimrg register "Card Underwriting PD" --owner credit-risk \
  --use credit_decision --category ml_non_generative
```

Or import an existing spreadsheet inventory (CSV or YAML, all-or-nothing):

```bash
aimrg import inventory.csv
```

CSV columns: `name, owner, business_uses, ai_category` (required) and `description,
vendor, depends_on` (optional). Separate several uses or dependencies with `;`.

### 3. Classify

```bash
aimrg classify "Card Underwriting PD"
```

The wizard asks whether the asset meets the SR 26-2 model definition, whether it
influences consequential consumer decisions, whether it is third-party, and which
jurisdictions it affects. Generative and agentic AI go to the **AI-system track**,
because SR 26-2 places them out of its scope. Every answer can also be given as an
option (`--model`, `--consequential`, `--in-house`, `--jurisdiction US-CO`,
`--rationale "..."`).

### 4. Assign a risk tier

```bash
aimrg tier "Card Underwriting PD"           # prompts for each factor, with guidance
aimrg tier "Card Underwriting PD" --score exposure=5 --score purpose=5 ...
```

Overrides need a justification and an approver:
`--override-tier 1 --justification "..." --approved-by "CRO"`.

### 5. See what's required

```bash
aimrg plan "Card Underwriting PD"
aimrg plan "Card Underwriting PD" --stage validation
```

Each applicable control is shown with its status (`missing`, `partial`, `failing`,
`complete`), the evidence still missing, and its automated checks.

### 6. Run checks

Checks work on **scored datasets**: CSV (or Parquet) files that already contain your
model's score and, where relevant, the observed outcome. Name each file by its role:

```bash
aimrg check run "Card Underwriting PD" FIN-VAL-003 \
  --data dev=dev.csv --data oot=oot.csv --target bad --score pd_score

aimrg check run "Card Underwriting PD" FIN-MON-001 \
  --data baseline=dev.csv --data current=2026q3.csv --target bad --score pd_score
```

Results are stored as evidence. A failing check opens a finding and exits with code 2,
which is useful in scheduled monitoring jobs. See [Checks](checks.md).

### 7. Record other evidence

```bash
aimrg evidence add "Card Underwriting PD" FIN-VAL-001 \
  --type document --summary "Validation memo v1" --file memo.pdf
aimrg evidence add "Card Underwriting PD" FIN-VAL-003 \
  --type attestation --summary "OOT results independently replicated"
```

### 8. Work findings

```bash
aimrg finding list
aimrg finding close 345e040b --evidence 9d008d13     # cite remediation evidence
aimrg finding accept 5b2e5e58 --by "Chief Risk Officer"
```

### 9. Generate documents

```bash
aimrg report render validation_report "Card Underwriting PD" --format docx
aimrg report render inventory_report -o inventory.md
```

Reports are drawn from recorded evidence. Judgments (conclusions, limitations, sign-off)
are left as `[TO COMPLETE]` placeholders for people to write.

### 10. Move through the lifecycle

```bash
aimrg status "Card Underwriting PD"
aimrg transition "Card Underwriting PD" development
aimrg transition "Card Underwriting PD" validation
aimrg transition "Card Underwriting PD" approved --approver "Model Risk Committee"
```

Each move has a gate (required evidence, no blocking findings, an independent approver).
See [Lifecycle workflow](workflow.md).

### 11. Fair lending and adverse-action reasons

```bash
aimrg check run "Card Underwriting PD" FIN-CPR-002 --data decisions=decisions.csv \
  --score pd_score --param group_prob_prefix=p_ --param control_group=group_a
aimrg check run "Card Underwriting PD" FIN-CPR-001 --data decisions=decisions.csv \
  --param reason_map=reasons.yaml
aimrg report render fair_lending_report "Card Underwriting PD" -o fair_lending.md
```

See [Checks](checks.md) for the parameters.

### 12. Verify the audit trail

```bash
aimrg audit verify
```

Recomputes every evidence hash chain and checks that stored artifacts are unchanged.

## From Python

Everything the CLI does is available as a library:

```python
from aimrg.checks import run_control_checks
from aimrg.registry import Registry
from aimrg.reports import render

with Registry.open("aimrg.db") as registry:
    runs = run_control_checks(
        registry,
        "Card Underwriting PD",
        "FIN-MON-001",
        {"baseline": dev_df, "current": current_df},
        actor="validator@bank",
        target="bad",
        score="pd_score",
    )
    markdown = render(registry, "validation_report", "Card Underwriting PD")
```

See `examples/credit-scorecard/run_example.py` for a complete script.
