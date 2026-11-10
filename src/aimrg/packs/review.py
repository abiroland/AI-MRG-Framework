"""Practitioner review of a pack: export a review workbook, and summarize returned ones.

Reviewers get one row per control with its objective, applicability, evidence and
citations, plus columns to record their judgment. Returned workbooks are summarized into
Markdown notes that can be published with the pack (``docs/reviews/``).
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aimrg.packs.models import ControlStage, Pack

#: Columns reviewers fill in, with the allowed answers ("" = free text).
REVIEW_COLUMNS: dict[str, tuple[str, ...]] = {
    "Citation accurate?": ("Yes", "No", "Unsure"),
    "Objective reflects the source?": ("Yes", "No", "Unsure"),
    "Applies to the right assets?": ("Yes", "No", "Unsure"),
    "Evidence appropriate?": ("Yes", "No", "Unsure"),
    "Issue severity": ("None", "Minor", "Major"),
    "Comments / suggested change": (),
}

CONTROL_COLUMNS = (
    "Control",
    "Area file",
    "Stage",
    "Title",
    "Objective",
    "Applies when",
    "Frequency",
    "Evidence required",
    "Automated checks",
    "References",
    "Guidance",
)

INSTRUCTIONS = (
    "Thank you for reviewing this governance pack.",
    "For each control, answer the four Yes/No/Unsure questions and rate any issue.",
    "Citation accurate: does the cited source and section actually support the control?",
    "Objective reflects the source: is the requirement stated correctly, neither stronger "
    "nor weaker than the source?",
    "Applies to the right assets: is the applicability rule (tier, track, uses, "
    "jurisdictions) right?",
    "Evidence appropriate: would the listed evidence satisfy an examiner or auditor?",
    "Use the Comments column for corrections, missing controls or better sources.",
    "Your review is not an endorsement. You choose how your name and affiliation appear.",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralize spreadsheet formula injection in text cells."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def control_rows(pack: Pack) -> list[dict[str, str]]:
    sources = {s.id: s for s in pack.sources}
    area = {}
    if pack.root:
        for path in sorted((Path(pack.root) / "controls").glob("*.y*ml")):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("- id:"):
                    area[stripped.split(":", 1)[1].strip()] = path.name
    order = {s: i for i, s in enumerate(ControlStage)}
    rows = []
    for c in sorted(pack.controls, key=lambda c: (order[c.lifecycle_stage], c.id)):
        refs = "; ".join(
            f"{r.source}{f' ({r.section})' if r.section else ''}: {sources[r.source].title}"
            if r.source in sources
            else r.source
            for r in c.references
        )
        rows.append(
            {
                "Control": c.id,
                "Area file": area.get(c.id, ""),
                "Stage": c.lifecycle_stage.value,
                "Title": c.title,
                "Objective": " ".join(c.objective.split()),
                "Applies when": c.applies_when,
                "Frequency": c.frequency.value,
                "Evidence required": ", ".join(e.value for e in c.evidence_required),
                "Automated checks": ", ".join(k.id for k in c.checks),
                "References": refs,
                "Guidance": " ".join(c.guidance.split()),
            }
        )
    return rows


def source_rows(pack: Pack) -> list[dict[str, str]]:
    return [
        {
            "Source": s.id,
            "Title": s.title,
            "Issuer": s.issuer,
            "Binding": s.binding.value,
            "Status": s.status,
            "Issued": s.issued.isoformat() if s.issued else "",
            "URL": s.url or "",
            "Notes": " ".join(s.notes.split()),
            "Status still accurate?": "",
            "Comments": "",
        }
        for s in pack.sources
    ]


def write_review_sheet(pack: Pack, path: str | Path) -> Path:
    """Write a review workbook (``.xlsx``, needs ``openpyxl``) or controls-only ``.csv``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = control_rows(pack)
    headers = [*CONTROL_COLUMNS, *REVIEW_COLUMNS]
    if out.suffix.lower() == ".csv":
        with out.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([safe_cell(row.get(h, "")) for h in headers])
        return out
    if out.suffix.lower() != ".xlsx":
        raise ValueError("review sheet must be .xlsx or .csv")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ValueError('.xlsx needs: pip install "ai-mrg[review]" (or use .csv)') from exc

    workbook = Workbook()
    intro = workbook.active
    intro.title = "Instructions"
    m = pack.manifest
    intro.append([f"Review of {m.title} — {m.ref} (content effective as of {m.effective_as_of})"])
    intro["A1"].font = Font(bold=True, size=13)
    for line in INSTRUCTIONS:
        intro.append([line])
    intro.append([])
    for label in (
        "Reviewer name (or 'Anonymous')",
        "Role",
        "Affiliation (optional)",
        "Date",
        "Scope reviewed",
        "Conflicts of interest",
    ):
        intro.append([label, ""])
    intro.column_dimensions["A"].width = 100
    intro.column_dimensions["B"].width = 40

    sheet = workbook.create_sheet("Controls")
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="DDEBF7")
    review_fill = PatternFill("solid", fgColor="FFF2CC")
    for col, name in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.fill = review_fill if name in REVIEW_COLUMNS else header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        sheet.append([safe_cell(row.get(h, "")) for h in headers])
    widths = {
        "Control": 13,
        "Area file": 18,
        "Stage": 14,
        "Title": 36,
        "Objective": 60,
        "Applies when": 34,
        "References": 60,
        "Guidance": 40,
        "Comments / suggested change": 50,
    }
    for col, name in enumerate(headers, start=1):
        letter = sheet.cell(row=1, column=col).column_letter
        sheet.column_dimensions[letter].width = widths.get(name, 18)
        if REVIEW_COLUMNS.get(name):
            validation = DataValidation(
                type="list", formula1='"' + ",".join(REVIEW_COLUMNS[name]) + '"', allow_blank=True
            )
            sheet.add_data_validation(validation)
            validation.add(f"{letter}2:{letter}{len(rows) + 1}")
    for row_cells in sheet.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "B2"

    sources = workbook.create_sheet("Sources")
    src_rows = source_rows(pack)
    src_headers = list(src_rows[0]) if src_rows else []
    sources.append(src_headers)
    for col in range(1, len(src_headers) + 1):
        sources.cell(row=1, column=col).font = Font(bold=True)
    for row in src_rows:
        sources.append([safe_cell(row[h]) for h in src_headers])
    workbook.save(out)
    return out


@dataclass
class ReviewSummary:
    reviewer: dict[str, str]
    answered: int
    total: int
    counts: dict[str, Counter[str]]
    flagged: list[dict[str, str]]
    sources_flagged: list[dict[str, str]]

    def to_markdown(self, pack_ref: str) -> str:
        r = self.reviewer
        lines = [
            f"# Review of `{pack_ref}`",
            "",
            "| | |",
            "|---|---|",
        ]
        for label in (
            "Reviewer name (or 'Anonymous')",
            "Role",
            "Affiliation (optional)",
            "Date",
            "Scope reviewed",
            "Conflicts of interest",
        ):
            value = (r.get(label) or "—").replace("|", "\\|")
            lines.append(f"| {label.split(' (')[0]} | {value} |")
        lines += ["", f"Controls with at least one answer: {self.answered} of {self.total}.", ""]
        lines += ["| Question | " + " | ".join(("Yes", "No", "Unsure")) + " |", "|---|---|---|---|"]
        for question, counter in self.counts.items():
            if question == "Issue severity":
                continue
            lines.append(
                f"| {question} | {counter.get('Yes', 0)} | {counter.get('No', 0)} | "
                f"{counter.get('Unsure', 0)} |"
            )
        severity = self.counts.get("Issue severity", Counter())
        lines += [
            "",
            f"Issues: {severity.get('Major', 0)} major, {severity.get('Minor', 0)} minor.",
            "",
        ]
        lines += ["## Controls flagged or commented", ""]
        if not self.flagged:
            lines.append("None.")
        for row in self.flagged:
            answers = ", ".join(
                f"{q.rstrip('?')}: {row.get(q)}"
                for q in REVIEW_COLUMNS
                if q != "Comments / suggested change"
                and row.get(q) not in (None, "", "Yes", "None")
            )
            lines.append(
                f"- **{row['Control']}** {row.get('Title', '')}"
                + (f" — {answers}" if answers else "")
            )
            comment = row.get("Comments / suggested change")
            if comment:
                lines.append(f"  > {comment}")
        if self.sources_flagged:
            lines += ["", "## Sources flagged", ""]
            for row in self.sources_flagged:
                lines.append(
                    f"- **{row['Source']}**: {row.get('Status still accurate?') or ''} "
                    f"{row.get('Comments') or ''}".rstrip()
                )
        lines += ["", "*A review is not an endorsement of AI-MRG or of any use of it.*", ""]
        return "\n".join(lines)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[1:] if text.startswith("'") else text


def summarize_review(path: str | Path) -> ReviewSummary:
    """Read a returned review workbook (``.xlsx``) or sheet (``.csv``)."""
    source = Path(path)
    reviewer: dict[str, str] = {}
    sources_rows: list[dict[str, str]] = []
    if source.suffix.lower() == ".csv":
        with source.open(newline="", encoding="utf-8-sig") as handle:
            control_data = [
                {k: _clean(v) for k, v in row.items()} for row in csv.DictReader(handle)
            ]
    else:
        from openpyxl import load_workbook

        workbook = load_workbook(source, read_only=True, data_only=True)
        for row in workbook["Instructions"].iter_rows(values_only=True):
            if row and row[0] and len(row) > 1 and row[1] is not None:
                reviewer[str(row[0])] = _clean(row[1])

        def records(name: str) -> list[dict[str, str]]:
            rows = list(workbook[name].iter_rows(values_only=True))
            if not rows:
                return []
            head = [str(h) for h in rows[0]]
            return [{h: _clean(v) for h, v in zip(head, r, strict=False)} for r in rows[1:]]

        control_data = records("Controls")
        sources_rows = [
            r
            for r in records("Sources")
            if r.get("Comments") or r.get("Status still accurate?") not in ("", "Yes")
        ]
        workbook.close()

    counts: dict[str, Counter[str]] = {q: Counter() for q in REVIEW_COLUMNS if REVIEW_COLUMNS[q]}
    flagged = []
    answered = 0
    for row in control_data:
        values = {q: row.get(q, "") for q in REVIEW_COLUMNS}
        if any(values.values()):
            answered += 1
        for q, allowed in REVIEW_COLUMNS.items():
            if allowed and values[q]:
                counts[q][values[q]] += 1
        problem = (
            any(
                values[q] in ("No", "Unsure")
                for q in REVIEW_COLUMNS
                if REVIEW_COLUMNS[q][:1] == ("Yes",)
            )
            or values["Issue severity"] in ("Minor", "Major")
            or values["Comments / suggested change"]
        )
        if problem:
            flagged.append(row)
    return ReviewSummary(
        reviewer=reviewer,
        answered=answered,
        total=len(control_data),
        counts=counts,
        flagged=flagged,
        sources_flagged=sources_rows,
    )
