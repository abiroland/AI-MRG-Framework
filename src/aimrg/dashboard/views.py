"""Pure helpers that turn API responses into tables and series for the dashboard."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd


def inventory_frame(assets: list[dict[str, Any]], findings: list[dict[str, Any]]) -> pd.DataFrame:
    open_by_asset = Counter(f["asset_id"] for f in findings)
    rows = []
    for a in assets:
        classification = a.get("classification") or {}
        risk = a.get("risk_assessment") or {}
        override = risk.get("override") or {}
        rows.append(
            {
                "Asset": a["name"],
                "Owner": a["owner"],
                "Technique": a["ai_category"],
                "Track": classification.get("track"),
                "Tier": override.get("tier", risk.get("computed_tier")),
                "Status": a["status"],
                "Vendor": a.get("vendor") or "",
                "Open findings": open_by_asset.get(a["id"], 0),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Asset",
            "Owner",
            "Technique",
            "Track",
            "Tier",
            "Status",
            "Vendor",
            "Open findings",
        ],
    )


def portfolio_metrics(
    assets: list[dict[str, Any]], findings: list[dict[str, Any]]
) -> dict[str, int]:
    frame = inventory_frame(assets, findings)
    return {
        "assets": len(frame),
        "tier_1": int((frame["Tier"] == 1).sum()),
        "ai_systems": int((frame["Track"] == "ai_system").sum()),
        "unclassified": int(frame["Track"].isna().sum()),
        "open_findings": len(findings),
        "in_production": int((frame["Status"] == "in_production").sum()),
    }


def coverage_frame(plan: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Control": p["control_id"],
                "Stage": p["stage"],
                "Status": p["status"]
                + (f" ({p['latest_outcome']})" if p["latest_outcome"] else ""),
                "Missing evidence": ", ".join(p["missing"]),
                "Checks": ", ".join(p["checks"]),
                "Title": p["title"],
            }
            for p in plan
        ],
        columns=["Control", "Stage", "Status", "Missing evidence", "Checks", "Title"],
    )


def coverage_counts(plan: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(p["status"] for p in plan)
    return {s: counts.get(s, 0) for s in ("complete", "partial", "failing", "missing")}


def evidence_frame(evidence: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Id": e["id"][:12],
                "Control": e["control_id"],
                "Type": e["type"],
                "Outcome": e.get("outcome"),
                "Actor": e["actor"],
                "Recorded": e["recorded_at"][:16].replace("T", " "),
                "Summary": e["summary"],
                "Attachment": "yes" if e.get("payload_uri") else "",
            }
            for e in evidence
        ],
        columns=["Id", "Control", "Type", "Outcome", "Actor", "Recorded", "Summary", "Attachment"],
    )


def findings_frame(findings: list[dict[str, Any]], names: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Id": f["id"][:8],
                "Asset": names.get(f["asset_id"], f["asset_id"][:8]),
                "Severity": f["severity"],
                "Status": f["status"],
                "Control": f.get("control_id"),
                "Due": f.get("due_date"),
                "Title": f["title"],
            }
            for f in findings
        ],
        columns=["Id", "Asset", "Severity", "Status", "Control", "Due", "Title"],
    )


def aging_frame(rows: list[dict[str, Any]], names: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Id": r["finding"]["id"][:8],
                "Asset": names.get(r["finding"]["asset_id"], ""),
                "Severity": r["finding"]["severity"],
                "Due": r["finding"].get("due_date"),
                "Days open": r["days_open"],
                "Days overdue": r["days_overdue"],
                "Bucket": r["bucket"],
                "Title": r["finding"]["title"],
            }
            for r in rows
        ],
        columns=["Id", "Asset", "Severity", "Due", "Days open", "Days overdue", "Bucket", "Title"],
    )


def numeric_metrics(results: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for r in results:
        names |= {k for k, v in r["metrics"].items() if isinstance(v, int | float)}
    return sorted(names)


def checks_by_runs(results: list[dict[str, Any]]) -> list[str]:
    """Check ids, most-run first (ties alphabetical), so trends show by default."""
    counts = Counter(r["check_id"] for r in results)
    return sorted(counts, key=lambda c: (-counts[c], c))


def trend_frame(results: list[dict[str, Any]], metric: str) -> pd.DataFrame:
    """One row per run that reported ``metric``: when, the value, the outcome."""
    rows = [
        {
            "Recorded": pd.Timestamp(r["recorded_at"]),
            metric: float(r["metrics"][metric]),
            "Outcome": r["outcome"],
            "Control": r["control_id"],
        }
        for r in results
        if isinstance(r["metrics"].get(metric), int | float)
    ]
    return pd.DataFrame(rows, columns=["Recorded", metric, "Outcome", "Control"])


def gate_lines(status: dict[str, Any]) -> list[tuple[str, bool, list[str]]]:
    return [(g["target"], g["allowed"], g["blockers"]) for g in status["next_steps"]]
