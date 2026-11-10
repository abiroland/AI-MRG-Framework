from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from conftest import REPO_ROOT, make_model_asset

from aimrg.core.models import AICategory, AssetRecord
from aimrg.registry import Registry, RegistryError
from aimrg.reports import DISCLAIMER, available_templates, markdown_to_docx, render


def test_templates_listed(registry: Registry) -> None:
    assert set(available_templates(registry)) >= {"validation_report", "inventory_report"}


def test_validation_report_needs_tiered_asset(registry: Registry) -> None:
    bare = registry.register(
        AssetRecord(name="Bare", owner="o", business_uses=["x"], ai_category=AICategory.TRADITIONAL)
    )
    with pytest.raises(RegistryError, match="cannot report"):
        render(registry, "validation_report", bare.id)
    with pytest.raises(RegistryError, match="needs an asset"):
        render(registry, "validation_report")
    with pytest.raises(RegistryError, match="no template"):
        render(registry, "nope")


def test_reports_render_and_never_claim_compliance(registry: Registry) -> None:
    asset = make_model_asset(registry, name="Pipe | Model")
    md = render(registry, "validation_report", asset.id)
    assert DISCLAIMER in md
    assert "Pipe \\| Model" in md  # escaped inside table cells
    assert "[TO COMPLETE" in md
    assert "is compliant" not in md.lower()
    inventory = render(registry, "inventory_report")
    assert "Pipe \\| Model" in inventory and "Not yet classified" in inventory


def test_new_templates_render_without_results(registry: Registry) -> None:
    asset = make_model_asset(registry, third_party=True)
    registry.save(registry.get(asset.id).model_copy(update={"vendor": "Acme Scores"}))
    fair = render(registry, "fair_lending_report", asset.id)
    assert "No fair-lending check results" in fair
    reasons = render(registry, "adverse_action_reasons", asset.id)
    assert "No adverse-action check results" in reasons
    vendor = render(registry, "vendor_assessment", asset.id)
    assert "Acme Scores" in vendor and "FIN-TPM-001" in vendor
    assert "No performance evidence" in vendor
    assert "No overdue findings" in render(registry, "inventory_report")


def test_markdown_to_docx(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    md = (
        "# Title\n\n> quoted **bold**\n\n## Section\n\n"
        "Some `code` and **bold** text\ncontinued.\n\n"
        "- bullet one\n- bullet two\n\n---\n\n| A | B |\n|---|---|\n| 1 | x \\| y |\n| 2 |\n"
    )
    out = markdown_to_docx(md, tmp_path / "sub" / "out.docx", title="T")
    document = docx.Document(str(out))
    texts = [p.text for p in document.paragraphs]
    assert "Title" in texts and "bullet one" in texts
    assert "Some code and bold text continued." in texts
    assert document.tables[0].rows[1].cells[1].text == "x | y"
    assert document.core_properties.title == "T"


def test_end_to_end_example(tmp_path: Path) -> None:
    script = REPO_ROOT / "examples" / "credit-scorecard" / "run_example.py"
    spec = importlib.util.spec_from_file_location("run_example", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registry = module.main(tmp_path / "demo")
    try:
        assert registry.verify().ok
        findings = registry.findings("Card Underwriting PD")
        assert len(findings) == 3
        assert sum(f.status.value == "open" for f in findings) == 2
        assert registry.get("Customer Service Assistant").classification.track.value == "ai_system"
        assert registry.get("Card Underwriting PD").status.value == "change_requested"
        demo = tmp_path / "demo"
        assert (demo / "validation_report.docx").is_file()
        report = (demo / "validation_report.md").read_text(encoding="utf-8")
        assert "performance.discrimination_drift — FAIL" in report
        assert "performance.out_of_time — PASS" in report
        assert "model-risk-committee@example-bank" in report  # lifecycle history
        fair = (demo / "fair_lending_report.md").read_text(encoding="utf-8")
        assert "fairness.standardized_mean_difference — FAIL" in fair
        assert "Screening metrics only" in fair
        reasons = (demo / "adverse_action_reasons.md").read_text(encoding="utf-8")
        assert "adverse_action.reason_specificity — PASS" in reasons
        assert "genai.agent_permission_audit — FAIL" in (demo / "ai_system_record.md").read_text(
            encoding="utf-8"
        )
        from aimrg.export import verify_package

        assert verify_package(demo / "examination-package.zip") == []
    finally:
        registry.close()
    with pytest.raises(SystemExit):
        module.main(tmp_path / "demo")
