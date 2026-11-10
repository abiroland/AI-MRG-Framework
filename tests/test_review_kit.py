from __future__ import annotations

import csv
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from aimrg._csv import safe_cell
from aimrg.cli import main
from aimrg.packs import load_builtin_pack
from aimrg.packs.models import PackManifest
from aimrg.packs.review import (
    REVIEW_COLUMNS,
    control_rows,
    summarize_review,
    write_review_sheet,
)

openpyxl = pytest.importorskip("openpyxl")


def test_manifest_reviewed_requires_reviews() -> None:
    base = {
        "name": "p",
        "version": "1.0.0",
        "effective_as_of": "2026-11-01",
        "sector": "financial_services",
        "title": "t",
        "maintainers": ["m"],
    }
    with pytest.raises(ValidationError, match="at least one review"):
        PackManifest.model_validate({**base, "reviewed": True})
    ok = PackManifest.model_validate(
        {
            **base,
            "reviewed": True,
            "reviews": [
                {
                    "reviewer": "Anonymous",
                    "role": "Model validator",
                    "date": "2026-12-01",
                    "scope": "All controls",
                }
            ],
        }
    )
    assert ok.reviews[0].conflicts == "none declared"


def test_safe_cell() -> None:
    assert safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert safe_cell("@SUM") == "'@SUM" and safe_cell("plain") == "plain" and safe_cell(3) == 3


def test_control_rows_are_ordered_and_complete() -> None:
    pack = load_builtin_pack("fin-us-banking")
    rows = control_rows(pack)
    assert len(rows) == len(pack.controls)
    assert rows[0]["Stage"] == "governance"
    sr = next(r for r in rows if r["Control"] == "FIN-VAL-001")
    assert "SR-26-2" in sr["References"] and sr["Area file"] == "validation.yaml"


def test_review_round_trip_xlsx(tmp_path: Path) -> None:
    pack = load_builtin_pack("fin-us-banking")
    path = write_review_sheet(pack, tmp_path / "review.xlsx")
    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames == ["Instructions", "Controls", "Sources"]
    controls = workbook["Controls"]
    headers = [c.value for c in controls[1]]
    col = {h: i + 1 for i, h in enumerate(headers)}
    assert controls.data_validations.dataValidation  # dropdowns present
    controls.cell(row=2, column=col["Citation accurate?"], value="No")
    controls.cell(row=2, column=col["Issue severity"], value="Major")
    controls.cell(row=2, column=col["Comments / suggested change"], value="Cite the section.")
    for q in (
        "Citation accurate?",
        "Objective reflects the source?",
        "Applies to the right assets?",
        "Evidence appropriate?",
    ):
        controls.cell(row=3, column=col[q], value="Yes")
    intro = workbook["Instructions"]
    for row in intro.iter_rows():
        if row[0].value == "Reviewer name (or 'Anonymous')":
            row[1].value = "Jane Doe"
        if row[0].value == "Role":
            row[1].value = "Validator | bank"
    sources = workbook["Sources"]
    sources.cell(row=2, column=9, value="No")
    sources.cell(row=2, column=10, value="Superseded")
    workbook.save(path)

    summary = summarize_review(path)
    assert summary.answered == 2 and summary.total == len(pack.controls)
    assert summary.counts["Citation accurate?"]["No"] == 1
    assert len(summary.flagged) == 1 and summary.sources_flagged
    md = summary.to_markdown(pack.manifest.ref)
    assert "Jane Doe" in md and "Validator \\| bank" in md
    assert "1 major" in md and "Cite the section." in md and "not an endorsement" in md


def test_review_csv_and_errors(tmp_path: Path) -> None:
    pack = load_builtin_pack("fin-us-banking")
    path = write_review_sheet(pack, tmp_path / "review.csv")
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert set(REVIEW_COLUMNS) <= set(rows[0])
    rows[0]["Comments / suggested change"] = "Typo in objective"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize_review(path)
    assert summary.flagged[0]["Comments / suggested change"] == "Typo in objective"
    assert "None." not in summary.to_markdown("x")
    with pytest.raises(ValueError, match=r"\.xlsx or \.csv"):
        write_review_sheet(pack, tmp_path / "review.txt")
    empty = summarize_review(write_review_sheet(pack, tmp_path / "empty.csv"))
    assert empty.answered == 0 and "None." in empty.to_markdown("x")


def test_review_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    sheet = tmp_path / "r.xlsx"
    out = runner.invoke(main, ["pack", "review-sheet", "fin-us-banking", "-o", str(sheet)])
    assert (out.exit_code == 0 and "36 controls" in out.output) or "controls" in out.output
    assert runner.invoke(main, ["pack", "review-sheet", "nope", "-o", str(sheet)]).exit_code != 0
    notes = tmp_path / "notes.md"
    out = runner.invoke(main, ["pack", "review-summary", str(sheet), "-o", str(notes)])
    assert out.exit_code == 0 and "0/" in out.output
    assert "# Review of" in runner.invoke(main, ["pack", "review-summary", str(sheet)]).output
    assert "NOT YET REVIEWED" in runner.invoke(main, ["pack", "list"]).output
