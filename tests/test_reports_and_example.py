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
        assert len(registry.findings()) == 2
        assert (tmp_path / "demo" / "validation_report.docx").is_file()
        report = (tmp_path / "demo" / "validation_report.md").read_text(encoding="utf-8")
        assert "performance.discrimination_drift — FAIL" in report
        assert "performance.out_of_time — PASS" in report
    finally:
        registry.close()
    with pytest.raises(SystemExit):
        module.main(tmp_path / "demo")
