from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner, Result

from aimrg.cli import main

ASSET = "Card PD"


@pytest.fixture
def cli(tmp_path: Path, portfolio: dict[str, pd.DataFrame], monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    for role, frame in portfolio.items():
        frame.to_csv(tmp_path / f"{role}.csv", index=False)
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None) -> Result:
        return runner.invoke(
            main,
            ["--db", "gov.db", "--actor", "tester", *args],
            input=input,
            catch_exceptions=False,
        )

    return invoke


def _ok(result: Result) -> Result:
    assert result.exit_code == 0, result.output
    return result


def test_full_cli_workflow(cli, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    assert "no registry" in cli("list").output
    _ok(cli("init"))
    assert cli("init").exit_code != 0  # already exists
    assert "No assets" in _ok(cli("list")).output

    _ok(
        cli(
            "register",
            "Feed",
            "--owner",
            "data",
            "--use",
            "feature_engineering",
            "--category",
            "traditional",
        )
    )
    out = _ok(
        cli(
            "register",
            ASSET,
            "--owner",
            "risk",
            "--use",
            "credit_decision",
            "--category",
            "ml_non_generative",
            "--depends-on",
            "Feed",
        )
    ).output
    assert 'aimrg classify "Card PD"' in out
    assert (
        cli(
            "register",
            "X",
            "--owner",
            "o",
            "--use",
            "u",
            "--category",
            "traditional",
            "--depends-on",
            "nope",
        ).exit_code
        != 0
    )

    # Interactive classification: model? consequential? third party? jurisdictions, rationale
    out = _ok(cli("classify", ASSET, input="y\ny\nn\nUS, US-CO\nPD model for approvals\n")).output
    assert "track = model" in out

    # Tiering: two factors given, the rest prompted.
    out = _ok(
        cli("tier", ASSET, "--score", "exposure=5", "--score", "purpose=5", input="3\n3\n2\n")
    ).output
    assert "tier 1" in out
    assert cli("tier", ASSET, "--score", "bad").exit_code != 0
    assert cli("tier", ASSET, "--profile", "nope").exit_code != 0
    assert cli("tier", ASSET, "--override-tier", "2", "--score", "exposure=1").exit_code != 0

    plan = _ok(cli("plan", ASSET, "--stage", "validation")).output
    assert "FIN-VAL-003" in plan and "missing" in plan

    out = _ok(
        cli(
            "check",
            "run",
            ASSET,
            "FIN-VAL-003",
            "--data",
            "dev=dev.csv",
            "--data",
            "oot=oot.csv",
            "--target",
            "bad",
            "--score",
            "pd_score",
        )
    ).output
    assert "[PASS ] performance.out_of_time" in out
    failing = cli(
        "check",
        "run",
        ASSET,
        "FIN-MON-001",
        "--data",
        "baseline=dev.csv",
        "--data",
        "current=current.csv",
        "--target",
        "bad",
        "--score",
        "pd_score",
    )
    assert failing.exit_code == 2 and "finding opened" in failing.output
    assert cli("check", "run", ASSET, "FIN-VAL-003", "--data", "nonsense").exit_code != 0
    assert cli("check", "run", ASSET, "FIN-VAL-003", "--data", "dev=missing.csv").exit_code != 0
    assert "outcomes.backtest" in _ok(cli("check", "list")).output

    Path("memo.md").write_text("memo", encoding="utf-8")
    _ok(
        cli(
            "evidence",
            "add",
            ASSET,
            "FIN-VAL-003",
            "--type",
            "attestation",
            "--summary",
            "replicated",
        )
    )
    out = _ok(
        cli(
            "evidence",
            "add",
            ASSET,
            "FIN-VAL-001",
            "--type",
            "document",
            "--summary",
            "memo",
            "--file",
            "memo.md",
        )
    ).output
    evidence_id = out.split("(")[-1].rstrip(")\n")
    assert (
        cli(
            "evidence", "add", ASSET, "FIN-NOPE-001", "--type", "document", "--summary", "x"
        ).exit_code
        != 0
    )
    listing = _ok(cli("evidence", "list", ASSET)).output
    assert "attestation" in listing and "check_result" in listing

    findings = _ok(cli("finding", "list")).output
    finding_id = findings.splitlines()[2].split()[0]
    _ok(
        cli(
            "finding",
            "open",
            ASSET,
            "--title",
            "Doc gap",
            "--severity",
            "low",
            "--due",
            "2027-01-31",
        )
    )
    assert "Doc gap" in _ok(cli("finding", "list", ASSET)).output
    assert cli("finding", "close", finding_id, "--evidence", "zzz").exit_code != 0
    _ok(cli("finding", "close", finding_id, "--evidence", evidence_id))
    doc_gap = next(
        line.split()[0]
        for line in _ok(cli("finding", "list")).output.splitlines()
        if "Doc gap" in line
    )
    _ok(cli("finding", "accept", doc_gap, "--by", "cro"))
    assert "closed" in _ok(cli("finding", "list", "--all")).output

    _ok(cli("report", "render", "validation_report", ASSET, "-o", "report.md"))
    assert "Model Validation Report" in Path("report.md").read_text(encoding="utf-8")
    assert "Inventory Report" in _ok(cli("report", "render", "inventory_report")).output
    _ok(cli("report", "render", "validation_report", ASSET, "--format", "docx"))
    assert list(Path().glob("validation_report-card-pd-*.docx"))
    assert "validation_report" in _ok(cli("report", "list")).output
    assert cli("report", "render", "validation_report").exit_code != 0

    assert "all chains intact" in _ok(cli("audit", "verify")).output
    listing = _ok(cli("list")).output
    assert ASSET in listing and "model" in listing
    assert "risk_assessment" in _ok(cli("show", ASSET)).output
    assert cli("show", "nope").exit_code != 0


def test_import_and_ai_system_classification(cli, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _ok(cli("init"))
    Path("inv.csv").write_text(
        "name,owner,business_uses,ai_category,vendor\n"
        "Helpdesk Bot,cx,customer_service,generative,LLM Co\n",
        encoding="utf-8",
    )
    assert "Imported 1" in _ok(cli("import", "inv.csv")).output
    assert cli("import", "inv.csv").exit_code != 0  # duplicate
    out = _ok(
        cli(
            "classify",
            "Helpdesk Bot",
            "--not-model",
            "--not-consequential",
            "--jurisdiction",
            "US",
            "--rationale",
            "LLM assistant",
        )
    ).output
    assert "ai_system" in out and "outside SR 26-2" in out
    _ok(
        cli(
            "tier",
            "Helpdesk Bot",
            *[
                f"--score={f}=2"
                for f in ("complexity", "assumptions", "data_quality", "exposure", "purpose")
            ],
        )
    )
    plan = _ok(cli("plan", "Helpdesk Bot")).output
    assert "FIN-AIS-001" in plan and "FIN-TPM-001" in plan and "FIN-VAL-001" not in plan
    assert cli("plan", "nope").exit_code != 0


def test_lifecycle_fairness_and_reasons_cli(cli, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from aimrg.datasets import synthetic_lending_decisions

    reasons_yaml = (
        Path(__file__).resolve().parents[1] / "examples" / "credit-scorecard" / "reasons.yaml"
    )
    synthetic_lending_decisions().to_csv("decisions.csv", index=False)
    _ok(cli("init"))
    _ok(
        cli(
            "register",
            ASSET,
            "--owner",
            "risk",
            "--use",
            "credit_decision",
            "--category",
            "ml_non_generative",
        )
    )
    _ok(
        cli(
            "classify",
            ASSET,
            "--model",
            "--consequential",
            "--in-house",
            "--jurisdiction",
            "US",
            "--rationale",
            "PD",
        )
    )
    _ok(
        cli(
            "tier",
            ASSET,
            *[
                f"--score={f}=4"
                for f in ("complexity", "assumptions", "data_quality", "exposure", "purpose")
            ],
        )
    )

    status = _ok(cli("status", ASSET)).output
    assert "intake" in status and "✓ development" in status
    _ok(cli("transition", ASSET, "development"))
    blocked = cli("transition", ASSET, "validation")
    assert blocked.exit_code != 0 and "FIN-DEV-001" in blocked.output
    assert "✗ validation" in _ok(cli("status", ASSET)).output

    out = cli(
        "check",
        "run",
        ASSET,
        "FIN-CPR-002",
        "--data",
        "decisions=decisions.csv",
        "--score",
        "pd_score",
        "--param",
        "group_col=group",
        "--param",
        "control_group=group_a",
    )
    assert out.exit_code == 2 and "fairness.adverse_impact_ratio" in out.output
    assert (
        cli(
            "check",
            "run",
            ASSET,
            "FIN-CPR-002",
            "--data",
            "decisions=decisions.csv",
            "--param",
            "oops",
        ).exit_code
        != 0
    )
    out = _ok(
        cli(
            "check",
            "run",
            ASSET,
            "FIN-CPR-001",
            "--data",
            "decisions=decisions.csv",
            "--param",
            f"reason_map={reasons_yaml}",
        )
    )
    assert "[PASS ] adverse_action.reason_mapping" in out.output

    aging = _ok(cli("finding", "aging")).output
    assert "standardized_mean_difference" in aging and "not due" in aging

    _ok(cli("reasons", "derive", "decisions.csv", "--map", str(reasons_yaml), "-o", "reasons.csv"))
    assert Path("reasons.csv").read_text(encoding="utf-8").startswith("row,reason_1")
    _ok(
        cli(
            "reasons",
            "derive",
            "decisions.csv",
            "--map",
            str(reasons_yaml),
            "--text",
            "--max-reasons",
            "2",
            "-o",
            "texts.csv",
        )
    )
    assert "Balances are high" in Path("texts.csv").read_text(encoding="utf-8")
    partial = Path("partial.yaml")
    partial.write_text(
        "reasons:\n  utilization: {code: R1, text: High balances relative to limits}\n"
    )
    out = _ok(
        cli("reasons", "derive", "decisions.csv", "--map", "partial.yaml", "-o", "p.csv")
    ).output
    assert "UNMAPPED" in out
    assert (
        cli(
            "reasons",
            "derive",
            "decisions.csv",
            "--map",
            "partial.yaml",
            "--adverse-col",
            "nope",
            "-o",
            "p.csv",
        ).exit_code
        != 0
    )
    assert (
        cli(
            "reasons",
            "derive",
            "decisions.csv",
            "--map",
            "partial.yaml",
            "--prefix",
            "shap_",
            "-o",
            "p.csv",
        ).exit_code
        != 0
    )

    _ok(cli("report", "render", "fair_lending_report", ASSET, "-o", "fair.md"))
    assert "AIR" in Path("fair.md").read_text(encoding="utf-8")
    for kind in ("document", "approval"):
        _ok(cli("evidence", "add", ASSET, "FIN-MON-004", "--type", kind, "--summary", "retire"))
    _ok(cli("transition", ASSET, "retired"))
    assert "No further transitions" in _ok(cli("status", ASSET)).output
    assert cli("transition", "nope", "development").exit_code != 0
    assert "No open findings" not in _ok(cli("finding", "aging", ASSET)).output


def test_audit_verify_reports_problems(cli, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _ok(cli("init"))
    _ok(cli("register", "A", "--owner", "o", "--use", "u", "--category", "traditional"))
    Path("f.txt").write_text("x", encoding="utf-8")
    _ok(
        cli(
            "evidence",
            "add",
            "A",
            "FIN-INV-001",
            "--type",
            "document",
            "--summary",
            "s",
            "--file",
            "f.txt",
        )
    )
    for artifact in Path("gov-artifacts").rglob("*.txt"):
        artifact.write_text("tampered", encoding="utf-8")
    result = cli("audit", "verify")
    assert result.exit_code == 1 and "artifact altered" in result.output


def test_init_postgres_via_cli(tmp_path: Path) -> None:
    from conftest import POSTGRES_URL, _postgres_database

    if not POSTGRES_URL:
        pytest.skip("set AIMRG_TEST_POSTGRES to run PostgreSQL tests")
    runner = CliRunner()
    with _postgres_database() as url:
        missing = runner.invoke(main, ["--db", url, "init"])
        assert missing.exit_code != 0 and "artifacts directory" in missing.output
        ok = runner.invoke(main, ["--db", url, "init", "--artifacts-dir", str(tmp_path / "a")])
        assert ok.exit_code == 0 and "PostgreSQL registry" in ok.output
        again = runner.invoke(main, ["--db", url, "init", "--artifacts-dir", str(tmp_path / "a")])
        assert again.exit_code != 0 and "already contains" in again.output
        reg = runner.invoke(
            main,
            [
                "--db",
                url,
                "register",
                "A",
                "--owner",
                "o",
                "--use",
                "u",
                "--category",
                "traditional",
            ],
        )
        assert reg.exit_code == 0
        assert "A" in runner.invoke(main, ["--db", url, "list"]).output


def test_init_if_missing_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    first = runner.invoke(main, ["--db", "gov.db", "init", "--if-missing"])
    assert first.exit_code == 0 and "Created" in first.output
    second = runner.invoke(main, ["--db", "gov.db", "init", "--if-missing"])
    assert second.exit_code == 0 and "already exists" in second.output
