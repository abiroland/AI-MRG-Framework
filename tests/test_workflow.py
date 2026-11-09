from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from conftest import make_model_asset

from aimrg.core.models import (
    AICategory,
    AssetRecord,
    EvidenceItem,
    EvidenceType,
    Finding,
    FindingStatus,
    LifecycleStatus,
    Severity,
)
from aimrg.findings import aging, default_due_date
from aimrg.packs.models import ControlStage
from aimrg.packs.resolver import required_controls
from aimrg.registry import Registry, RegistryError
from aimrg.workflow import WorkflowError, check_transition, next_steps, transition


def _complete_stage(registry: Registry, asset_id: str, stage: ControlStage, actor: str) -> None:
    """Record every evidence type each control in ``stage`` requires."""
    asset = registry.get(asset_id)
    for control in required_controls(registry.pack, asset, stage):
        for kind in control.evidence_required:
            registry.add_evidence(
                EvidenceItem(
                    asset_id=asset_id,
                    control_id=control.id,
                    type=kind,
                    check_id=f"x.{control.id.lower().replace('-', '_')}"
                    if kind is EvidenceType.CHECK_RESULT
                    else None,
                    outcome="pass" if kind is EvidenceType.CHECK_RESULT else None,
                    pack_version=registry.pack.manifest.ref,
                    actor=actor,
                )
            )


@pytest.fixture
def model(registry: Registry) -> str:
    return make_model_asset(registry).id


def test_unclassified_assets_cannot_start_development(registry: Registry) -> None:
    bare = registry.register(
        AssetRecord(name="Bare", owner="o", business_uses=["x"], ai_category=AICategory.TRADITIONAL)
    )
    gate = check_transition(registry, bare.id, "development")
    assert not gate.allowed and "classified" in gate.blockers[0]
    assert check_transition(registry, bare.id, "retired").allowed


def test_disallowed_moves(registry: Registry, model: str) -> None:
    gate = check_transition(registry, model, "in_production")
    assert not gate.allowed and "cannot move from intake" in gate.blockers[0]


def test_full_lifecycle_with_gates(registry: Registry, model: str) -> None:
    transition(registry, model, "development", actor="dev")
    with pytest.raises(WorkflowError) as excinfo:
        transition(registry, model, "validation", actor="dev")
    assert any("FIN-DEV-001" in b for b in excinfo.value.blockers)

    _complete_stage(registry, model, ControlStage.DEVELOPMENT, "dev")
    transition(registry, model, "validation", actor="dev")

    _complete_stage(registry, model, ControlStage.VALIDATION, "validator")
    # Separation of duties.
    owner = registry.get(model).owner
    assert any(
        "owner" in b for b in check_transition(registry, model, "approved", approver=owner).blockers
    )
    assert any(
        "development evidence" in b
        for b in check_transition(registry, model, "approved", approver="dev").blockers
    )
    assert any(
        "approver is required" in b for b in check_transition(registry, model, "approved").blockers
    )

    # An open high finding blocks approval.
    finding = registry.add_finding(
        Finding(asset_id=model, severity=Severity.HIGH, title="Gap", owner="o")
    )
    assert any(
        "open high finding" in b
        for b in check_transition(registry, model, "approved", approver="cro").blockers
    )
    registry.save_finding(
        finding.model_copy(
            update={"status": FindingStatus.RISK_ACCEPTED, "risk_acceptance_by": "cro"}
        )
    )

    event = transition(registry, model, "approved", actor="validator", approver="cro")
    assert event.approver == "cro"
    approvals = registry.evidence(model, control_id="FIN-VAL-004")
    assert approvals and approvals[0].type is EvidenceType.APPROVAL and approvals[0].actor == "cro"

    with pytest.raises(WorkflowError, match="FIN-CPR-004"):
        transition(registry, model, "in_production", actor="dev")
    _complete_stage(registry, model, ControlStage.IMPLEMENTATION, "dev")
    transition(registry, model, "in_production", actor="dev")

    assert "note" in check_transition(registry, model, "change_requested").blockers[0]
    transition(registry, model, "change_requested", actor="v", note="recalibrate")
    transition(registry, model, "validation", actor="dev")
    history = registry.events(model)
    assert [e.to_status for e in history][-1] is LifecycleStatus.VALIDATION
    assert len(history) == 6


def test_approve_with_limits_and_reject(registry: Registry, model: str) -> None:
    transition(registry, model, "development", actor="dev")
    _complete_stage(registry, model, ControlStage.DEVELOPMENT, "dev")
    transition(registry, model, "validation", actor="dev")

    gate = check_transition(registry, model, "approved_with_limits", approver="cro")
    assert gate.blockers == ["approval with limits needs the limits documented (--limits)"]
    registry.add_finding(
        Finding(asset_id=model, severity=Severity.CRITICAL, title="Bad", owner="o")
    )
    assert any(
        "critical" in b
        for b in check_transition(
            registry, model, "approved_with_limits", approver="cro", limits="x"
        ).blockers
    )
    for f in registry.findings(model):
        registry.save_finding(
            f.model_copy(
                update={"status": FindingStatus.RISK_ACCEPTED, "risk_acceptance_by": "cro"}
            )
        )
    event = transition(
        registry,
        model,
        "approved_with_limits",
        actor="v",
        approver="cro",
        limits="Max line $5,000; monthly monitoring until validation completes",
    )
    assert event.limits and "monthly" in event.limits
    summary = registry.evidence(model, control_id="FIN-VAL-004")[-1].summary
    assert summary.startswith("Approved with limits")


def test_reject_needs_note(registry: Registry, model: str) -> None:
    transition(registry, model, "development", actor="dev")
    _complete_stage(registry, model, ControlStage.DEVELOPMENT, "dev")
    transition(registry, model, "validation", actor="dev")
    assert any(
        "note" in b for b in check_transition(registry, model, "rejected", approver="cro").blockers
    )
    transition(registry, model, "rejected", actor="v", approver="cro", note="unstable")
    gates = {g.target.value: g for g in next_steps(registry, model)}
    assert set(gates) == {"development", "retired"}


def test_failing_control_cleared_by_resolved_finding(registry: Registry, model: str) -> None:
    transition(registry, model, "development", actor="dev")
    _complete_stage(registry, model, ControlStage.DEVELOPMENT, "dev")
    registry.add_evidence(
        EvidenceItem(
            asset_id=model,
            control_id="FIN-DEV-002",
            type=EvidenceType.CHECK_RESULT,
            check_id="x.fin_dev_002",
            outcome="fail",
            pack_version=registry.pack.manifest.ref,
            actor="dev",
        )
    )
    assert any("FIN-DEV-002" in b for b in check_transition(registry, model, "validation").blockers)
    finding = registry.add_finding(
        Finding(
            asset_id=model,
            control_id="FIN-DEV-002",
            severity=Severity.MEDIUM,
            title="dq",
            owner="o",
        )
    )
    assert any("FIN-DEV-002" in b for b in check_transition(registry, model, "validation").blockers)
    registry.save_finding(
        finding.model_copy(update={"status": FindingStatus.CLOSED, "remediation_evidence": ["e"]})
    )
    assert check_transition(registry, model, "validation").allowed


def test_record_event_checks_current_status(registry: Registry, model: str) -> None:
    from aimrg.core.models import LifecycleEvent

    with pytest.raises(RegistryError, match="is intake"):
        registry.record_event(
            LifecycleEvent(
                asset_id=model, from_status="validation", to_status="approved", actor="x"
            )
        )
    transition(registry, model, "development", actor="dev")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        registry._conn.execute("DELETE FROM events")


def test_retired_is_terminal(registry: Registry, model: str) -> None:
    assert any("FIN-MON-004" in b for b in check_transition(registry, model, "retired").blockers)
    _complete_stage(registry, model, ControlStage.RETIREMENT, "o")
    transition(registry, model, "retired", actor="o")
    assert next_steps(registry, model) == []


def test_migration_from_schema_v1(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    Registry.create(path).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER events_no_update")
    conn.execute("DROP TRIGGER events_no_delete")
    conn.execute("DROP TABLE events")
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with Registry.open(path) as reg:
        assert reg.meta("schema_version") == "2"
        reg.register(
            AssetRecord(name="A", owner="o", business_uses=["x"], ai_category="traditional")
        )
        assert reg.events("A") == []


# --- findings aging -------------------------------------------------------------------------


def test_default_due_dates_and_aging() -> None:
    today = date(2026, 6, 1)
    assert default_due_date(Severity.HIGH, today) == today + timedelta(days=60)

    def finding(severity: Severity, opened_days_ago: int, **kw: object) -> Finding:
        return Finding.model_validate(
            {
                "asset_id": "a",
                "severity": severity,
                "title": "t",
                "owner": "o",
                "opened_at": datetime.combine(
                    today - timedelta(days=opened_days_ago), datetime.min.time(), UTC
                ),
                **kw,
            }
        )

    rows = aging(
        [
            finding(Severity.CRITICAL, 200),  # due after 30 → 170 overdue
            finding(Severity.LOW, 10),  # not due
            finding(Severity.HIGH, 100),  # due after 60 → 40 overdue
            finding(Severity.MEDIUM, 95, due_date=today - timedelta(days=5)),
            finding(Severity.LOW, 500, status="closed", remediation_evidence=["e"]),
        ],
        today=today,
    )
    assert [r.bucket for r in rows] == ["90+ overdue", "31-90 overdue", "1-30 overdue", "not due"]
    assert rows[0].days_overdue == 170 and rows[-1].days_overdue == 0
