from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from aimrg.api import create_app
from aimrg.dashboard import APP_PATH, views
from aimrg.dashboard.client import ApiClient, ApiError

REPO = Path(__file__).resolve().parents[1]
ASSET = "Card Underwriting PD"


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "run_example", REPO / "examples" / "credit-scorecard" / "run_example.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path_factory.mktemp("demo")
    registry = module.main(out)
    tokens = {
        "val": registry.add_user("val", ["validator"])[1],
        "mrc": registry.add_user("mrc", ["approver", "auditor"])[1],
    }
    location = registry.location
    registry.close()
    return location, tokens


def _client(demo, who: str = "val") -> ApiClient:  # type: ignore[no-untyped-def]
    location, tokens = demo
    return ApiClient(token=tokens[who], http=TestClient(create_app(location)))


def _app(client: ApiClient) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["client"] = client
    return at.run()


def test_client_round_trip(demo) -> None:  # type: ignore[no-untyped-def]
    client = _client(demo)
    assert client.health()["status"] == "ok"
    assert client.me()["name"] == "val"
    assert {a["name"] for a in client.assets()} >= {ASSET}
    results = client.check_results(ASSET, "outcomes.backtest")
    assert results and results[0]["metrics"]["relative_error"] > 0
    assert set(client.templates()) == {"asset", "portfolio"}
    assert b"Inventory" in client.report("inventory_report")
    with pytest.raises(ApiError) as excinfo:
        client.transition(ASSET, "approved")  # validators cannot approve
    assert excinfo.value.status == 403
    unauthenticated = ApiClient(http=TestClient(create_app(demo[0])))
    with pytest.raises(ApiError) as excinfo:
        unauthenticated.me()
    assert excinfo.value.status == 401


def test_workflow_blockers_render_in_errors(demo) -> None:  # type: ignore[no-untyped-def]
    error = ApiError(409, {"message": "cannot move", "blockers": ["FIN-VAL-001 is missing"]})
    assert "cannot move" in str(error) and "- FIN-VAL-001" in str(error)
    assert str(ApiError(400, "plain")) == "plain"


def test_views() -> None:
    assets = [
        {
            "id": "a",
            "name": "A",
            "owner": "o",
            "ai_category": "traditional",
            "status": "intake",
            "classification": None,
            "risk_assessment": None,
        },
        {
            "id": "b",
            "name": "B",
            "owner": "o",
            "ai_category": "agentic",
            "status": "in_production",
            "classification": {"track": "ai_system"},
            "risk_assessment": {"computed_tier": 2, "override": {"tier": 1}},
        },
    ]
    findings = [
        {
            "asset_id": "b",
            "id": "f1",
            "severity": "high",
            "status": "open",
            "title": "t",
            "control_id": None,
            "due_date": None,
        }
    ]
    frame = views.inventory_frame(assets, findings)
    assert frame.loc[1, "Tier"] == 1 and frame.loc[1, "Open findings"] == 1
    assert views.portfolio_metrics(assets, findings) == {
        "assets": 2,
        "tier_1": 1,
        "ai_systems": 1,
        "unclassified": 1,
        "open_findings": 1,
        "in_production": 1,
    }
    results = [
        {
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "metrics": {"psi": 0.1, "note": "x"},
            "outcome": "pass",
            "control_id": "C",
        },
        {
            "recorded_at": "2026-02-01T00:00:00+00:00",
            "metrics": {"psi": 0.3},
            "outcome": "fail",
            "control_id": "C",
        },
    ]
    assert views.numeric_metrics(results) == ["psi"]
    assert list(views.trend_frame(results, "psi")["psi"]) == [0.1, 0.3]
    assert views.findings_frame(findings, {"b": "B"}).loc[0, "Asset"] == "B"


def test_dashboard_pages(demo) -> None:  # type: ignore[no-untyped-def]
    at = _app(_client(demo, "mrc"))
    assert not at.exception
    assert at.header[0].value == "Inventory"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Assets"] == "3" and metrics["AI systems"] == "1"

    at.sidebar.radio(key="page").set_value("Asset").run()
    assert not at.exception
    at.selectbox(key="asset_select").set_value(ASSET).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Status"] == "change_requested"
    assert any("✓ validation" in s.value for s in at.success)

    at.sidebar.radio(key="page").set_value("Findings").run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Open"] == "4"

    at.sidebar.radio(key="page").set_value("Monitoring").run()
    assert not at.exception
    at.selectbox(key="mon_asset").set_value(ASSET).run()
    at.selectbox(key="mon_check").set_value("outcomes.backtest").run()
    assert not at.exception

    at.sidebar.radio(key="page").set_value("Reports").run()
    assert not at.exception
    at.selectbox(key="rep_template").set_value("validation_report").run()
    assert not at.exception
    at.button(key="build_pkg").click().run()
    assert not at.exception
    assert any("SHA256SUMS" in m.value for m in at.markdown)


def test_dashboard_actions_go_through_the_api(demo) -> None:  # type: ignore[no-untyped-def]
    at = _app(_client(demo, "val"))
    at.sidebar.radio(key="page").set_value("Asset").run()
    at.selectbox(key="asset_select").set_value(ASSET).run()

    # Record an attestation through the form.
    at.selectbox(key="ev_control").set_value("FIN-MON-001")
    at.selectbox(key="ev_type").set_value("attestation")
    at.text_input(key="ev_summary").set_value("Reviewed monitoring results")
    at.button(key="FormSubmitter:add_evidence-Record").click().run()
    assert not at.exception
    assert any("Evidence recorded" in s.value for s in at.success)

    # A validator cannot accept risk: the API refuses and the page shows why.
    at.button(key="FormSubmitter:resolve_finding-Accept risk").click().run()
    assert any("requires role: approver" in e.value for e in at.error)

    # Moving to validation is allowed from change_requested.
    at.selectbox(key="t_target").set_value("validation")
    at.button(key="FormSubmitter:transition-Submit").click().run()
    assert any("Moved to validation" in s.value for s in at.success)


def test_dashboard_without_client_shows_connect() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60).run()
    assert not at.exception
    assert any("Enter the API URL" in i.value for i in at.info)
    at.sidebar.text_input[1].set_value("aimrg_bad")
    at.sidebar.button(key="connect").click().run()
    assert any("Could not connect" in e.value for e in at.error)
