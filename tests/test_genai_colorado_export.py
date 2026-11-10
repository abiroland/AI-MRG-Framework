from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner
from conftest import make_model_asset

from aimrg.checks import CheckContext, run_check, run_control_checks
from aimrg.checks.genai import NIST_600_1_RISKS
from aimrg.cli import main
from aimrg.core.classify import classify
from aimrg.core.models import AICategory, AssetRecord, CheckOutcome
from aimrg.datasets import SCORE, TARGET, synthetic_genai_evidence
from aimrg.export import export_package, reports_for, verify_package
from aimrg.packs import load_builtin_pack
from aimrg.packs.resolver import required_controls
from aimrg.registry import Registry, RegistryError
from aimrg.reports import render
from aimrg.tiering import assess_asset


@pytest.fixture(scope="module")
def genai() -> dict[str, pd.DataFrame]:
    return synthetic_genai_evidence(seed=7)


def _ctx(datasets: dict[str, pd.DataFrame], **params: object) -> CheckContext:
    return CheckContext(
        datasets=datasets,
        params=dict(params),
        thresholds=dict(load_builtin_pack("fin-us-banking").thresholds),
    )


# --- GenAI checks ------------------------------------------------------------------------


def test_nist_600_1_has_twelve_risks() -> None:
    assert len(NIST_600_1_RISKS) == 12 and "Confabulation" in NIST_600_1_RISKS


def test_evaluation_suite(genai: dict[str, pd.DataFrame]) -> None:
    result = run_check(
        "genai.evaluation_suite",
        _ctx(
            {"evaluations": genai["evaluations"]},
            required_categories="groundedness,prompt_injection",
        ),
    )
    assert result.outcome is CheckOutcome.FAIL  # prompt injection 90% < 95% fail level
    by_name = {t.name: t for t in result.thresholds}
    assert by_name["pass rate (prompt_injection)"].fail == 0.95  # category-specific threshold
    assert by_name["pass rate (groundedness)"].outcome is CheckOutcome.PASS
    rows = {r["category"]: r for r in result.tables["categories"]}
    assert rows["groundedness"]["nist_600_1_risk"] == "Confabulation"


def test_evaluation_suite_missing_and_small_categories() -> None:
    df = pd.DataFrame({"category": ["groundedness"] * 10, "passed": [1] * 10})
    result = run_check(
        "genai.evaluation_suite",
        _ctx({"evaluations": df}, required_categories=["groundedness", "privacy_leakage"]),
    )
    assert result.outcome is CheckOutcome.FAIL
    assert "privacy_leakage" in result.notes[0]
    assert any("only 10" in n for n in result.notes)
    ok_small = run_check("genai.evaluation_suite", _ctx({"evaluations": df}))
    assert ok_small.outcome is CheckOutcome.WARN
    bad = run_check("genai.evaluation_suite", _ctx({"evaluations": df.assign(passed=[2] * 10)}))
    assert bad.outcome is CheckOutcome.ERROR


def test_red_team_log(genai: dict[str, pd.DataFrame]) -> None:
    result = run_check("genai.red_team_log", _ctx({"red_team": genai["red_team"]}))
    assert result.outcome is CheckOutcome.WARN and result.metrics["open_medium"] == 1
    critical = genai["red_team"].copy()
    critical.loc[0, "status"] = "open"
    assert (
        run_check("genai.red_team_log", _ctx({"red_team": critical})).outcome is CheckOutcome.FAIL
    )
    fixed = genai["red_team"].assign(status="fixed")
    assert run_check("genai.red_team_log", _ctx({"red_team": fixed})).outcome is CheckOutcome.PASS
    empty = run_check("genai.red_team_log", _ctx({"red_team": genai["red_team"].head(0)}))
    assert empty.outcome is CheckOutcome.WARN and "empty" in empty.summary
    weird = run_check(
        "genai.red_team_log", _ctx({"red_team": genai["red_team"].assign(severity="severe")})
    )
    assert weird.outcome is CheckOutcome.ERROR


def test_agent_permission_audit(genai: dict[str, pd.DataFrame]) -> None:
    actions = {"agent_actions": genai["agent_actions"]}
    result = run_check(
        "genai.agent_permission_audit",
        _ctx(
            actions,
            allowed_tools="kb_search,account_lookup,create_ticket,issue_refund",
            human_approval_tools="issue_refund",
        ),
    )
    assert result.outcome is CheckOutcome.FAIL and result.metrics["unapproved_actions"] == 1
    narrow = run_check(
        "genai.agent_permission_audit", _ctx(actions, allowed_tools="kb_search,unused_tool")
    )
    assert narrow.metrics["unauthorized_actions"] > 0
    assert any("unused_tool" in n for n in narrow.notes)
    clean = genai["agent_actions"].assign(human_approved=1)
    ok = run_check(
        "genai.agent_permission_audit",
        _ctx(
            {"agent_actions": clean},
            allowed_tools=["kb_search", "account_lookup", "create_ticket", "issue_refund"],
            human_approval_tools="issue_refund",
        ),
    )
    assert ok.outcome is CheckOutcome.PASS
    assert run_check("genai.agent_permission_audit", _ctx(actions)).outcome is CheckOutcome.ERROR


def test_alternative_assumptions(portfolio: dict[str, pd.DataFrame]) -> None:
    dev = portfolio["dev"].assign(
        alt_no_income=portfolio["dev"][SCORE] * 0.9 + 0.01,
        alt_noise=portfolio["dev"][SCORE].sample(frac=1, random_state=1).to_numpy(),
    )
    ctx = CheckContext(
        datasets={"dev": dev},
        target=TARGET,
        score=SCORE,
        params={"alternative_scores": "alt_no_income,alt_noise"},
    )
    result = run_check("sensitivity.alternative_assumptions", ctx)
    assert result.outcome is CheckOutcome.INFO
    rows = {r["specification"]: r for r in result.tables["specifications"]}
    assert rows["alt_no_income"]["rank_correlation"] == pytest.approx(1.0)
    assert rows["alt_noise"]["gini"] < rows[SCORE]["gini"]
    better = dev.assign(alt_oracle=dev[TARGET] + 0.0)
    ctx2 = CheckContext(
        datasets={"dev": better},
        target=TARGET,
        score=SCORE,
        params={"alternative_scores": "alt_oracle"},
    )
    assert any(
        "discriminates better" in n
        for n in run_check("sensitivity.alternative_assumptions", ctx2).notes
    )
    missing = CheckContext(datasets={"dev": dev}, target=TARGET, score=SCORE)
    assert run_check("sensitivity.alternative_assumptions", missing).outcome is CheckOutcome.ERROR
    one_class = CheckContext(
        datasets={"dev": dev.assign(**{TARGET: 0})},
        target=TARGET,
        score=SCORE,
        params={"alternative_scores": "alt_noise"},
    )
    assert run_check("sensitivity.alternative_assumptions", one_class).outcome is CheckOutcome.ERROR


def test_every_pack_check_is_implemented() -> None:
    from aimrg.checks import available_checks

    pack = load_builtin_pack("fin-us-banking")
    referenced = {c.id for control in pack.controls for c in control.checks}
    assert referenced <= set(available_checks())


# --- AI-system and Colorado content ---------------------------------------------------------


def _assistant(registry: Registry, **classify_kw: object) -> AssetRecord:
    asset = registry.register(
        AssetRecord(
            name="Assistant",
            owner="cx",
            business_uses=["customer_service"],
            ai_category=AICategory.AGENTIC,
            vendor="LLM Co",
        )
    )
    options = {
        "is_model_sr26_2": False,
        "rationale": "agent",
        "third_party": True,
        "jurisdictions": ["US"],
    }
    options.update(classify_kw)
    registry.set_classification(asset.id, classify(ai_category="agentic", **options))
    return assess_asset(
        registry,
        asset.id,
        {f: 3 for f in ("complexity", "assumptions", "data_quality", "exposure", "purpose")},
        assessed_by="v",
    )


def test_ai_system_record(registry: Registry, genai: dict[str, pd.DataFrame]) -> None:
    asset = _assistant(registry)
    empty = render(registry, "ai_system_record", asset.id)
    assert "No AI-system check results" in empty
    assert all(risk in empty for risk in NIST_600_1_RISKS)
    run_control_checks(
        registry,
        asset.id,
        "FIN-AIS-003",
        {"evaluations": genai["evaluations"], "red_team": genai["red_team"]},
        actor="v",
    )
    run_control_checks(
        registry,
        asset.id,
        "FIN-AIS-004",
        {"agent_actions": genai["agent_actions"]},
        actor="v",
        params={
            "allowed_tools": "kb_search,account_lookup,create_ticket,issue_refund",
            "human_approval_tools": "issue_refund",
        },
    )
    md = render(registry, "ai_system_record", asset.id)
    assert "genai.evaluation_suite — FAIL" in md
    assert "| issue_refund |" in md and "| critical |" in md
    assert "genai.agent_permission_audit failed" in md  # incident/finding table


def test_colorado_controls_apply_only_to_colorado_consequential(registry: Registry) -> None:
    co = make_model_asset(
        registry, name="CO model", jurisdictions=["US", "US-CO"], third_party=True
    )
    us = make_model_asset(registry, name="US model")
    co_ids = {c.id for c in required_controls(registry.pack, co)}
    assert {"FIN-CPR-005", "FIN-CPR-006", "FIN-CPR-007", "FIN-CPR-008", "FIN-TPM-004"} <= co_ids
    assert not {"FIN-CPR-005", "FIN-TPM-004"} & {c.id for c in required_controls(registry.pack, us)}
    notices = render(registry, "colorado_admt_notices", co.id)
    assert "Draft for counsel review" in notices and "FIN-CPR-006" in notices
    assert "do not apply" in render(registry, "colorado_admt_notices", us.id)
    assert reports_for(registry.get(co.id))[-1] == "colorado_admt_notices"


# --- examination packages -------------------------------------------------------------------


def test_export_and_verify(registry: Registry, tmp_path: Path, genai) -> None:  # type: ignore[no-untyped-def]
    model = make_model_asset(registry, name="PD Model", jurisdictions=["US", "US-CO"])
    assistant = _assistant(registry)
    registry.register(
        AssetRecord(name="Untiered", owner="o", business_uses=["x"], ai_category="traditional")
    )
    run_control_checks(
        registry,
        assistant.id,
        "FIN-AIS-003",
        {"evaluations": genai["evaluations"], "red_team": genai["red_team"]},
        actor="v",
    )
    summary = export_package(registry, tmp_path / "pkg.zip")
    assert summary.assets == 3 and summary.audit_ok
    assert any("Untiered" in s for s in summary.skipped_reports)
    assert verify_package(summary.path) == []
    assert verify_package(summary.path, summary.sums_sha256) == []
    assert "does not match" in verify_package(summary.path, "0" * 64)[0]

    with zipfile.ZipFile(summary.path) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
        evidence_lines = z.read("records/evidence/assistant.jsonl").decode().splitlines()
    assert manifest["format"] == "aimrg-examination-package"
    assert {a["name"] for a in manifest["assets"]} == {"PD Model", "Assistant", "Untiered"}
    assert "reports/pd-model/colorado_admt_notices.md" in names
    assert "reports/assistant/ai_system_record.md" in names
    assert "reports/assistant/vendor_assessment.md" in names
    assert "pack/pack.yaml" in names and "pack/controls/ai_systems.yaml" in names
    assert any(n.startswith("artifacts/") for n in names)
    assert len(evidence_lines) == 2

    only = export_package(registry, tmp_path / "one.zip", [model.name])
    assert only.assets == 1

    # Tampering is detected.
    def rewrite(target: Path, change: dict[str, bytes | None]) -> None:
        with zipfile.ZipFile(summary.path) as src, zipfile.ZipFile(target, "w") as dst:
            for item in src.namelist():
                if item in change:
                    if change[item] is not None:
                        dst.writestr(item, change[item])
                else:
                    dst.writestr(item, src.read(item))
            for item, content in change.items():
                if item not in src.namelist() and content is not None:
                    dst.writestr(item, content)

    rewrite(tmp_path / "a.zip", {"reports/inventory_report.md": b"edited"})
    assert verify_package(tmp_path / "a.zip") == ["altered file: reports/inventory_report.md"]
    rewrite(tmp_path / "b.zip", {"manifest.json": None})
    assert verify_package(tmp_path / "b.zip") == ["missing file: manifest.json"]
    rewrite(tmp_path / "c.zip", {"SHA256SUMS": None})
    assert verify_package(tmp_path / "c.zip") == ["SHA256SUMS is missing"]
    assert "cannot open" in verify_package(tmp_path / "nope.zip")[0]


def test_export_empty_registry(registry: Registry, tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="no assets"):
        export_package(registry, tmp_path / "x.zip")


def test_export_reports_missing_artifacts(registry: Registry, tmp_path: Path) -> None:
    from aimrg.core.models import EvidenceItem

    asset = make_model_asset(registry)
    uri, digest = registry.store_artifact(b"doc", ".md")
    registry.add_evidence(
        EvidenceItem(
            asset_id=asset.id,
            control_id="FIN-VAL-001",
            type="document",
            payload_uri=uri,
            payload_sha256=digest,
            pack_version=registry.pack.manifest.ref,
            actor="v",
        )
    )
    registry.artifact_path(uri).unlink()
    summary = export_package(registry, tmp_path / "pkg.zip")
    assert not summary.audit_ok
    with zipfile.ZipFile(summary.path) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["missing_artifacts"] == [uri]
    assert verify_package(summary.path) == []


def test_export_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def cli(*args: str):  # type: ignore[no-untyped-def]
        return runner.invoke(main, ["--db", "gov.db", *args], catch_exceptions=False)

    cli("init")
    assert cli("export", "create", "-o", "p.zip").exit_code != 0
    cli("register", "A", "--owner", "o", "--use", "u", "--category", "traditional")
    out = cli("export", "create", "-o", "p.zip")
    assert out.exit_code == 0 and "SHA-256 of SHA256SUMS" in out.output and "skipped" in out.output
    assert "OK" in cli("export", "verify", "p.zip").output
    assert cli("export", "verify", "p.zip", "--expect", "0" * 64).exit_code == 1
