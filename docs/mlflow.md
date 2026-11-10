# MLflow integration

Most data-science teams already track models in MLflow. AI-MRG can take its inventory
from there and push governance status back, so developers see it where they work.

```bash
pip install -e ".[mlflow]"          # mlflow-skinny is enough
export MLFLOW_TRACKING_URI=https://mlflow.internal.example
```

## Import registered models

```bash
aimrg mlflow import --default-owner model-risk --default-use credit_decision
```

Every registered model not yet in the inventory is registered. Tags on the registered
model fill in the details; anything missing uses the defaults:

| MLflow registered-model tag | AI-MRG field |
|---|---|
| `aimrg.owner` | owner |
| `aimrg.business_uses` | business uses (comma-separated) |
| `aimrg.ai_category` | technique: `traditional`, `ml_non_generative`, `generative`, `agentic` |
| `aimrg.vendor` | vendor (third-party models) |

The asset remembers its MLflow model (and the run behind its latest version) in
`external_refs`. Imported assets still need classification and tiering: those are
judgments, not metadata.

## Push governance status to MLflow

```bash
aimrg mlflow sync "card_pd"
```

Sets these tags on the registered model and on the run behind its latest version (or
`--run-id`):

`aimrg.asset_id`, `aimrg.status`, `aimrg.track`, `aimrg.tier`,
`aimrg.controls_complete` (e.g. `14/22`), `aimrg.open_findings`, `aimrg.pack`

It also logs the asset's main report (validation report, or AI-system record) as the run
artifact `aimrg/<report>.md`. Use `--no-report` to skip that.

## Record a run as evidence

```bash
aimrg mlflow record "card_pd" FIN-DEV-003 4f2c9a…
```

Stores a snapshot of the run's parameters, metrics and tags as a document artifact, and
records it as evidence for the control. The snapshot is hashed like any other evidence,
so later changes in MLflow don't alter what was recorded.

## From Python

```python
from aimrg.integrations import mlflow as aimrg_mlflow
from aimrg.registry import Registry

with Registry.open("aimrg.db") as registry:
    aimrg_mlflow.import_registered_models(registry, tracking_uri=uri)
    aimrg_mlflow.sync_to_mlflow(registry, "card_pd", tracking_uri=uri)
```
