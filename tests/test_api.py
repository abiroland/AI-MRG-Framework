from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from aimrg.api import create_app
from aimrg.datasets import FEATURES, SCORE, TARGET
from aimrg.registry import Registry
from aimrg.registry.accounts import Role

ASSET = "Card PD"


@pytest.fixture
def api(registry: Registry):  # type: ignore[no-untyped-def]
    tokens = {
        name: registry.add_user(name, roles)[1]
        for name, roles in {
            "dana": [Role.DEVELOPER],
            "val": [Role.VALIDATOR],
            "apo": [Role.APPROVER],
            "vic": [Role.VIEWER],
            "aud": [Role.AUDITOR],
            "root": [Role.ADMIN],
        }.items()
    }
    client = TestClient(create_app(registry.location))

    def call(method: str, path: str, who: str | None = None, **kw):  # type: ignore[no-untyped-def]
        headers = {"Authorization": f"Bearer {tokens[who]}"} if who else {}
        headers.update(kw.pop("headers", {}))
        return client.request(method, path, headers=headers, **kw)

    return call


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def test_auth(api, registry: Registry) -> None:  # type: ignore[no-untyped-def]
    assert api("GET", "/health").json()["status"] == "ok"
    assert api("GET", "/me").status_code == 401
    bad = api("GET", "/me", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    me = api("GET", "/me", "val").json()
    assert me["name"] == "val" and me["roles"] == ["validator"]
    registry.set_user_active("vic", False)
    assert api("GET", "/assets", "vic").status_code == 401


def test_roles_are_enforced(api) -> None:  # type: ignore[no-untyped-def]
    body = {"name": "X", "owner": "o", "business_uses": ["u"], "ai_category": "traditional"}
    assert api("POST", "/assets", "vic", json=body).status_code == 403
    assert api("POST", "/assets", "root", json=body).status_code == 201
    assert (
        api(
            "PUT",
            "/assets/X/classification",
            "dana",
            json={"is_model_sr26_2": True, "rationale": "r"},
        ).status_code
        == 403
    )
    assert api("GET", "/audit/verify", "val").status_code == 403
    assert api("GET", "/audit/verify", "aud").json()["ok"] is True
    assert api("GET", "/assets/missing", "vic").status_code == 404


def test_full_flow_over_api(api, registry: Registry, portfolio) -> None:  # type: ignore[no-untyped-def]
    r = api(
        "POST",
        "/assets",
        "dana",
        json={
            "name": ASSET,
            "owner": "credit-risk",
            "business_uses": ["credit_decision"],
            "ai_category": "ml_non_generative",
        },
    )
    assert r.status_code == 201, r.text
    assert (
        api(
            "POST",
            "/assets",
            "dana",
            json={
                "name": ASSET,
                "owner": "o",
                "business_uses": ["u"],
                "ai_category": "traditional",
            },
        ).status_code
        == 400
    )

    r = api(
        "PUT",
        f"/assets/{ASSET}/classification",
        "val",
        json={
            "is_model_sr26_2": True,
            "consequential_decision": True,
            "jurisdictions": ["US"],
            "rationale": "PD model",
        },
    )
    assert r.json()["classification"]["track"] == "model"
    scores = {f: 4 for f in ("complexity", "assumptions", "data_quality", "exposure", "purpose")}
    r = api("PUT", f"/assets/{ASSET}/risk-assessment", "val", json={"factor_scores": scores})
    assert r.json()["risk_assessment"]["computed_tier"] == 1
    assert (
        api(
            "PUT",
            f"/assets/{ASSET}/risk-assessment",
            "val",
            json={"factor_scores": scores, "profile_id": "nope"},
        ).status_code
        == 400
    )
    assert (
        api(
            "PUT", f"/assets/{ASSET}/risk-assessment", "val", json={"factor_scores": {"x": 1}}
        ).status_code
        == 400
    )

    plan = api("GET", f"/assets/{ASSET}/plan?stage=development", "vic").json()
    assert {p["control_id"] for p in plan} == {"FIN-DEV-001", "FIN-DEV-002", "FIN-DEV-003"}

    # Lifecycle: developer moves to development and records development evidence.
    assert (
        api(
            "POST", f"/assets/{ASSET}/transitions", "dana", json={"status": "development"}
        ).status_code
        == 201
    )
    small = {k: v.sample(800, random_state=1) for k, v in portfolio.items()}
    files = {
        role: (f"{role}.csv", _csv(small[role]), "text/csv") for role in ("dev", "test", "oot")
    }
    runs = api(
        "POST",
        f"/assets/{ASSET}/checks/FIN-DEV-003",
        "dana",
        files=files,
        data={"target": TARGET, "score": SCORE, "features": ",".join(FEATURES)},
    ).json()
    assert {r["check_id"]: r["outcome"] for r in runs}["performance.out_of_sample"] in (
        "pass",
        "warn",
        "fail",
    )
    files = {"dev": ("dev.csv", _csv(small["dev"])), "population": ("p.csv", _csv(small["oot"]))}
    api(
        "POST",
        f"/assets/{ASSET}/checks/FIN-DEV-002",
        "dana",
        files=files,
        data={"target": TARGET, "score": SCORE, "features": ",".join(FEATURES)},
    )
    for control in ("FIN-DEV-001", "FIN-DEV-002", "FIN-DEV-003"):
        r = api(
            "POST",
            f"/assets/{ASSET}/evidence",
            "dana",
            data={"control_id": control, "type": "document", "summary": "doc"},
            files={"file": ("doc.md", b"# doc", "text/markdown")},
        )
        assert r.status_code == 201, r.text
        assert r.json()["actor"] == "dana" and r.json()["payload_uri"].endswith(".md")
    assert (
        api(
            "POST",
            f"/assets/{ASSET}/evidence",
            "dana",
            data={"control_id": "FIN-DEV-001", "type": "approval", "summary": "x"},
        ).status_code
        == 400
    )
    assert (
        api(
            "POST",
            f"/assets/{ASSET}/evidence",
            "dana",
            data={"control_id": "FIN-NOPE-001", "type": "document", "summary": "x"},
        ).status_code
        == 400
    )

    # Remaining development failures (if any) must be resolved before moving on.
    for f in api("GET", f"/findings?asset={ASSET}", "vic").json():
        ev = api("GET", f"/assets/{ASSET}/evidence?control_id={f['control_id']}", "vic").json()
        assert (
            api(
                "POST", f"/findings/{f['id']}/close", "val", json={"evidence_ids": [ev[-1]["id"]]}
            ).status_code
            == 200
        )
    r = api("POST", f"/assets/{ASSET}/transitions", "dana", json={"status": "validation"})
    assert r.status_code == 201, r.text

    # Approval is blocked until validation evidence exists; the blockers are returned.
    r = api("POST", f"/assets/{ASSET}/transitions", "apo", json={"status": "approved"})
    assert r.status_code == 409
    assert any("FIN-VAL-001" in b for b in r.json()["detail"]["blockers"])
    assert (
        api("POST", f"/assets/{ASSET}/transitions", "val", json={"status": "approved"}).status_code
        == 403
    )

    status = api("GET", f"/assets/{ASSET}/status", "vic").json()
    assert status["status"] == "validation" and len(status["history"]) == 2
    assert not next(g for g in status["next_steps"] if g["target"] == "approved")["allowed"]

    # Findings via the API.
    f = api(
        "POST", f"/assets/{ASSET}/findings", "val", json={"title": "Doc gap", "severity": "low"}
    ).json()
    assert f["due_date"]
    assert api("POST", f"/findings/{f['id']}/accept", "val").status_code == 403
    assert api("POST", f"/findings/{f['id']}/accept", "apo").json()["risk_acceptance_by"] == "apo"
    assert (
        api(
            "POST", f"/findings/{f['id']}/close", "val", json={"evidence_ids": ["nope"]}
        ).status_code
        == 400
    )
    assert isinstance(api("GET", "/findings/aging", "vic").json(), list)
    assert len(api("GET", "/findings?open_only=false", "vic").json()) >= 1

    # Reports.
    md = api("GET", f"/assets/{ASSET}/reports/validation_report", "vic")
    assert md.status_code == 200 and "Model Validation Report" in md.text
    docx = api("GET", "/reports/inventory_report?format=docx", "vic")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert api("GET", "/reports/nope", "vic").status_code == 404

    assert api("GET", "/audit/verify", "root").json()["ok"] is True
    assert {e["actor"] for e in api("GET", f"/assets/{ASSET}/evidence", "vic").json()} == {"dana"}


def test_approver_independence_over_api(api, registry: Registry) -> None:  # type: ignore[no-untyped-def]
    """An approver who recorded development evidence cannot approve that asset."""
    from conftest import make_model_asset

    from aimrg.core.models import EvidenceItem
    from aimrg.packs.models import ControlStage
    from aimrg.packs.resolver import required_controls
    from aimrg.workflow import transition

    asset = make_model_asset(registry, name="Indep")
    transition(registry, asset.id, "development", actor="dana")
    for stage, actor in ((ControlStage.DEVELOPMENT, "apo"), (ControlStage.VALIDATION, "val")):
        for control in required_controls(registry.pack, registry.get(asset.id), stage):
            for kind in control.evidence_required:
                registry.add_evidence(
                    EvidenceItem(
                        asset_id=asset.id,
                        control_id=control.id,
                        type=kind,
                        check_id="x.y" if kind.value == "check_result" else None,
                        outcome="pass" if kind.value == "check_result" else None,
                        pack_version=registry.pack.manifest.ref,
                        actor=actor,
                    )
                )
        if stage is ControlStage.DEVELOPMENT:
            transition(registry, asset.id, "validation", actor="dana")
    r = api("POST", "/assets/Indep/transitions", "apo", json={"status": "approved"})
    assert r.status_code == 409
    assert any("development evidence" in b for b in r.json()["detail"]["blockers"])
    registry.add_user("apo2", ["approver"])


def test_check_upload_errors(api) -> None:  # type: ignore[no-untyped-def]
    api(
        "POST",
        "/assets",
        "dana",
        json={"name": "A", "owner": "o", "business_uses": ["u"], "ai_category": "traditional"},
    )
    assert api("POST", "/assets/A/checks/FIN-DEV-002", "dana", data={}).status_code == 400
    bad_json = api(
        "POST",
        "/assets/A/checks/FIN-DEV-002",
        "dana",
        files={"dev": ("d.csv", b"a,b\n1,2\n")},
        data={"params": "{oops"},
    )
    assert bad_json.status_code == 400
    bad_csv = api(
        "POST", "/assets/A/checks/FIN-DEV-002", "dana", files={"dev": ("d.csv", io.BytesIO(b""))}
    )
    assert bad_csv.status_code == 400
    unknown = api(
        "POST", "/assets/A/checks/FIN-ZZZ-001", "dana", files={"dev": ("d.csv", b"a,b\n1,2\n")}
    )
    assert unknown.status_code == 400
    assert json.loads(unknown.text)["detail"].startswith("unknown control")


def test_service_unavailable_without_registry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(create_app(str(tmp_path / "missing.db")))
    assert client.get("/me", headers={"Authorization": "Bearer x"}).status_code == 503
