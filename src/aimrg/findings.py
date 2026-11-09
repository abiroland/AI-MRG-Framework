"""Finding due dates and aging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from aimrg.core.models import Finding, FindingStatus, Severity

#: Default remediation windows in days. Starting points; set your own policy.
DEFAULT_DUE_DAYS: dict[Severity, int] = {
    Severity.CRITICAL: 30,
    Severity.HIGH: 60,
    Severity.MEDIUM: 90,
    Severity.LOW: 180,
}


def default_due_date(severity: Severity, opened: date | None = None) -> date:
    return (opened or date.today()) + timedelta(days=DEFAULT_DUE_DAYS[severity])


@dataclass(frozen=True)
class AgingRow:
    finding: Finding
    days_open: int
    days_overdue: int
    bucket: str


def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "not due"
    if days_overdue <= 30:
        return "1-30 overdue"
    if days_overdue <= 90:
        return "31-90 overdue"
    return "90+ overdue"


def aging(findings: list[Finding], today: date | None = None) -> list[AgingRow]:
    """Open findings with their age and how overdue they are, most overdue first."""
    today = today or date.today()
    rows = []
    for f in findings:
        if f.status not in (FindingStatus.OPEN, FindingStatus.IN_REMEDIATION):
            continue
        due = f.due_date or default_due_date(f.severity, f.opened_at.date())
        overdue = (today - due).days
        rows.append(
            AgingRow(
                finding=f,
                days_open=(today - f.opened_at.date()).days,
                days_overdue=max(overdue, 0),
                bucket=_bucket(overdue),
            )
        )
    return sorted(rows, key=lambda r: (-r.days_overdue, r.finding.opened_at))
