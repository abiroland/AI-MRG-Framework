from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

mlflow = pytest.importorskip("mlflow")

from click.testing import CliRunner  # noqa: E402
from conftest import make_model_asset  # noqa: E402

from aimrg.cli import main  # noqa: E402
from aimrg.core.models import AICategory  # noqa: E402
from aimrg.integrations import mlflow as integration  # noqa: E402
from aimrg.registry import Registry, RegistryError  # noqa: E402


@pytest.fixture
def tracking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Any, str]:
    """A local MLflow store with one tagged and one untagged registered model."""
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    uri = (tmp_path / "mlruns").as_uri()
    client = mlflow.MlflowClient(tracking_uri=uri, registry_uri=uri)
    experiment = client.create_experiment("card")
    run_id = client.create_run(experiment).info.run_id
    client.log_param(run_id, "max_depth", 4)
    client.log_metric(run_id, "auc", 0.72)
    client.set_terminated(run_id)
    client.create_registered_model(
        "card_pd",
        tags={"aimrg.owner": "credit-risk", "aimrg.business_uses": "credit_decision, pricing"},
        description="PD model",
    )
    client.create_model_version("card_pd", source=f"runs:/{run_id}/model", run_id=run_id)
    client.create_registered_model(
        "helpdesk_llm", tags={"aimrg.ai_category": "generative", "aimrg.vendor": "LLM Co"}
    )
    return uri, client, run_id


def test_import_registered_models(registry: Registry, tracking) -> None:  # type: ignore[no-untyped-def]
    uri, _, run_id = tracking
    result = integration.import_registered_models(
        registry, tracking_uri=uri, default_owner="mrm", default_uses=["customer_service"]
    )
    assert {a.name for a in result.created} == {"card_pd", "helpdesk_llm"}
    pd_model = registry.get("card_pd")
    assert pd_model.owner == "credit-risk"
    assert pd_model.business_uses == ["credit_decision", "pricing"]
    assert pd_model.external_refs == {"mlflow.registered_model": "card_pd", "mlflow.run_id": run_id}
    llm = registry.get("helpdesk_llm")
    assert llm.ai_category is AICategory.GENERATIVE and llm.vendor == "LLM Co"
    assert llm.owner == "mrm" and llm.business_uses == ["customer_service"]
    again = integration.import_registered_models(registry, tracking_uri=uri)
    assert again.created == [] and set(again.skipped) == {"card_pd", "helpdesk_llm"}
    filtered = integration.import_registered_models(registry, tracking_uri=uri, name_filter="zzz")
    assert filtered.created == [] and filtered.skipped == []


def test_sync_and_record(registry: Registry, tracking) -> None:  # type: ignore[no-untyped-def]
    uri, client, run_id = tracking
    integration.import_registered_models(registry, tracking_uri=uri)
    asset = registry.get("card_pd")
    from aimrg.core.classify import classify
    from aimrg.tiering import assess_asset

    registry.set_classification(
        asset.id,
        classify(
            ai_category="ml_non_generative",
            is_model_sr26_2=True,
            consequential_decision=True,
            rationale="PD",
        ),
    )
    assess_asset(
        registry,
        asset.id,
        {f: 4 for f in ("complexity", "assumptions", "data_quality", "exposure", "purpose")},
        assessed_by="v",
    )

    result = integration.sync_to_mlflow(registry, "card_pd", tracking_uri=uri)
    assert result.run_id == run_id and result.artifacts == ["aimrg/validation_report.md"]
    run_tags = client.get_run(run_id).data.tags
    assert run_tags["aimrg.tier"] == "1" and run_tags["aimrg.status"] == "intake"
    complete, required = run_tags["aimrg.controls_complete"].split("/")
    assert int(complete) == 0 and int(required) > 10
    assert client.get_registered_model("card_pd").tags["aimrg.track"] == "model"
    assert [f.path for f in client.list_artifacts(run_id, "aimrg")] == [
        "aimrg/validation_report.md"
    ]

    item = integration.record_run_as_evidence(
        registry, "card_pd", "FIN-DEV-003", run_id, actor="dev", tracking_uri=uri
    )
    snapshot = json.loads(registry.read_artifact(item.payload_uri or ""))
    assert snapshot["metrics"] == {"auc": 0.72} and snapshot["params"] == {"max_depth": "4"}

    unlinked = make_model_asset(registry, name="Unlinked")
    with pytest.raises(RegistryError, match="no MLflow link"):
        integration.sync_to_mlflow(registry, unlinked.id, tracking_uri=uri)
    plain = integration.sync_to_mlflow(
        registry, unlinked.id, tracking_uri=uri, run_id=run_id, include_report=False
    )
    assert plain.model is None and plain.artifacts == []

    llm = registry.get("helpdesk_llm")  # untiered, no versions → tags on the model only
    assert integration.sync_to_mlflow(registry, llm.id, tracking_uri=uri).run_id is None
    assert "aimrg.tier" not in integration.governance_tags(registry, llm)


def test_mlflow_cli(tmp_path: Path, tracking, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    uri, _, run_id = tracking
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def cli(*args: str):  # type: ignore[no-untyped-def]
        return runner.invoke(
            main, ["--db", "gov.db", "--actor", "dev", *args], catch_exceptions=False
        )

    cli("init")
    out = cli(
        "mlflow",
        "--tracking-uri",
        uri,
        "import",
        "--default-owner",
        "mrm",
        "--default-use",
        "customer_service",
    )
    assert out.exit_code == 0 and "2 imported" in out.output
    assert "already in the inventory" in cli("mlflow", "--tracking-uri", uri, "import").output
    out = cli("mlflow", "--tracking-uri", uri, "sync", "card_pd", "--no-report")
    assert out.exit_code == 0 and "aimrg.status = intake" in out.output
    out = cli("mlflow", "--tracking-uri", uri, "record", "card_pd", "FIN-DEV-003", run_id)
    assert out.exit_code == 0 and "MLflow run" in out.output
    cli("register", "X", "--owner", "o", "--use", "u", "--category", "traditional")
    assert cli("mlflow", "--tracking-uri", uri, "sync", "X").exit_code != 0
    assert (
        cli("mlflow", "--tracking-uri", uri, "record", "card_pd", "FIN-NOPE-001", run_id).exit_code
        != 0
    )
