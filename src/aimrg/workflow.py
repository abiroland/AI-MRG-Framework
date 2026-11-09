"""Lifecycle workflow: gated status transitions with separation of duties.

Every status change goes through :func:`transition`, which checks the gate for that
move and records an append-only :class:`~aimrg.core.models.LifecycleEvent`.

Gates (in addition to the move being allowed from the current status). A control whose
latest automated check failed counts as satisfied for a gate only if the findings raised
against that control have all been closed or risk-accepted.

====================== ===============================================================
to                     requires
====================== ===============================================================
development            the asset is classified and risk-tiered
validation             all development-stage controls complete
approved               all validation-stage controls complete; no open high/critical
                       findings; an independent approver
approved_with_limits   documented limits; no open critical findings; an independent
                       approver (the urgent-use path SR 26-2 allows before full
                       validation, with limits and heightened monitoring)
rejected               an independent approver and a note
in_production          all implementation-stage controls complete
change_requested       a note describing the change
retired                all retirement-stage controls complete
====================== ===============================================================

*Independent approver*: not the asset owner and not anyone who recorded
development-stage evidence for the asset. Approvals are also written to the evidence
trail against the pack's approval-stage controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aimrg.core.models import (
    EvidenceItem,
    EvidenceType,
    FindingStatus,
    LifecycleEvent,
    LifecycleStatus,
    Severity,
)
from aimrg.packs.models import ControlStage
from aimrg.packs.resolver import CoverageStatus, ResolverError, coverage
from aimrg.registry.store import Registry, RegistryError

S = LifecycleStatus

ALLOWED: dict[LifecycleStatus, tuple[LifecycleStatus, ...]] = {
    S.INTAKE: (S.DEVELOPMENT, S.RETIRED),
    S.DEVELOPMENT: (S.VALIDATION, S.RETIRED),
    S.VALIDATION: (S.APPROVED, S.APPROVED_WITH_LIMITS, S.REJECTED, S.DEVELOPMENT),
    S.APPROVED: (S.IN_PRODUCTION, S.RETIRED),
    S.APPROVED_WITH_LIMITS: (S.APPROVED, S.IN_PRODUCTION, S.RETIRED),
    S.REJECTED: (S.DEVELOPMENT, S.RETIRED),
    S.IN_PRODUCTION: (S.CHANGE_REQUESTED, S.RETIRED),
    S.CHANGE_REQUESTED: (S.VALIDATION, S.IN_PRODUCTION),
    S.RETIRED: (),
}

_APPROVALS = (S.APPROVED, S.APPROVED_WITH_LIMITS, S.REJECTED)
_STAGE_GATES: dict[LifecycleStatus, ControlStage] = {
    S.VALIDATION: ControlStage.DEVELOPMENT,
    S.APPROVED: ControlStage.VALIDATION,
    S.IN_PRODUCTION: ControlStage.IMPLEMENTATION,
    S.RETIRED: ControlStage.RETIREMENT,
}


class WorkflowError(RegistryError):
    def __init__(self, message: str, blockers: list[str] | None = None) -> None:
        self.blockers = blockers or []
        detail = "".join(f"\n  - {b}" for b in self.blockers)
        super().__init__(message + detail)


@dataclass
class Gate:
    target: LifecycleStatus
    blockers: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.blockers


def check_transition(
    registry: Registry,
    ref: str,
    target: LifecycleStatus | str,
    *,
    approver: str | None = None,
    limits: str | None = None,
    note: str | None = None,
) -> Gate:
    """Work out whether ``ref`` may move to ``target``, and if not, why."""
    asset = registry.get(ref)
    to = LifecycleStatus(target)
    gate = Gate(target=to)
    if to not in ALLOWED[asset.status]:
        allowed = ", ".join(s.value for s in ALLOWED[asset.status]) or "none"
        gate.blockers.append(
            f"cannot move from {asset.status.value} to {to.value} (allowed: {allowed})"
        )
        return gate

    if asset.classification is None or asset.risk_assessment is None:
        if to is not S.RETIRED:
            gate.blockers.append("the asset must be classified and risk-tiered first")
            return gate
        return gate

    evidence = registry.evidence(asset.id)
    findings = registry.findings(asset.id)
    resolved = {FindingStatus.CLOSED, FindingStatus.RISK_ACCEPTED}
    stage = _STAGE_GATES.get(to)
    if to is S.APPROVED_WITH_LIMITS:
        stage = None  # the urgent-use path does not require complete validation
    if stage is not None:
        try:
            items = coverage(registry.pack, asset, evidence, stage)
        except ResolverError as exc:  # pragma: no cover - guarded above
            gate.blockers.append(str(exc))
            return gate
        for item in items:
            if item.status is CoverageStatus.FAILING:
                related = [f for f in findings if f.control_id == item.control.id]
                if related and all(f.status in resolved for f in related):
                    continue  # the failure was dealt with through a finding
            if item.status is not CoverageStatus.COMPLETE:
                missing = ", ".join(t.value for t in item.missing)
                detail = f"missing {missing}" if missing else item.status.value
                if item.status is CoverageStatus.FAILING and item.latest_outcome:
                    detail = f"latest check {item.latest_outcome.value}"
                gate.blockers.append(
                    f"{item.control.id} ({stage.value}) is {item.status.value}: {detail}"
                )

    open_findings = [f for f in findings if f.status not in resolved]
    if to is S.APPROVED:
        blocking = [f for f in open_findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
        gate.blockers += [
            f"open {f.severity.value} finding {f.id[:8]}: {f.title}" for f in blocking
        ]
    if to is S.APPROVED_WITH_LIMITS:
        blocking = [f for f in open_findings if f.severity is Severity.CRITICAL]
        gate.blockers += [f"open critical finding {f.id[:8]}: {f.title}" for f in blocking]
        if not (limits and limits.strip()):
            gate.blockers.append("approval with limits needs the limits documented (--limits)")

    if to in _APPROVALS:
        if not (approver and approver.strip()):
            gate.blockers.append("an approver is required (--approver)")
        else:
            developers = {
                e.actor
                for e in evidence
                if _stage_of(registry, e.control_id) is ControlStage.DEVELOPMENT
            }
            if approver == asset.owner:
                gate.blockers.append(f"approver {approver!r} is the asset owner")
            if approver in developers:
                gate.blockers.append(
                    f"approver {approver!r} recorded development evidence for this asset"
                )
    if to in (S.REJECTED, S.CHANGE_REQUESTED) and not (note and note.strip()):
        gate.blockers.append("a note explaining the decision is required (--note)")
    return gate


def _stage_of(registry: Registry, control_id: str) -> ControlStage | None:
    try:
        return registry.pack.control(control_id).lifecycle_stage
    except KeyError:
        return None


def transition(
    registry: Registry,
    ref: str,
    target: LifecycleStatus | str,
    *,
    actor: str,
    approver: str | None = None,
    limits: str | None = None,
    note: str = "",
) -> LifecycleEvent:
    """Move an asset to ``target`` if its gate allows; record the event and approvals."""
    asset = registry.get(ref)
    gate = check_transition(registry, ref, target, approver=approver, limits=limits, note=note)
    if not gate.allowed:
        raise WorkflowError(f"{asset.name!r} cannot move to {gate.target.value}:", gate.blockers)
    event = registry.record_event(
        LifecycleEvent(
            asset_id=asset.id,
            from_status=asset.status,
            to_status=gate.target,
            actor=actor,
            approver=approver,
            note=note,
            limits=limits,
        )
    )
    if gate.target in _APPROVALS:
        decision = {
            S.APPROVED: "Approved",
            S.APPROVED_WITH_LIMITS: f"Approved with limits: {limits}",
            S.REJECTED: f"Rejected: {note}",
        }[gate.target]
        refreshed = registry.get(asset.id)
        for item in coverage(registry.pack, refreshed, [], ControlStage.APPROVAL):
            if EvidenceType.APPROVAL in item.control.evidence_required:
                registry.add_evidence(
                    EvidenceItem(
                        asset_id=asset.id,
                        control_id=item.control.id,
                        type=EvidenceType.APPROVAL,
                        summary=f"{decision} (lifecycle event {event.id[:12]})",
                        pack_version=registry.pack.manifest.ref,
                        actor=approver or actor,
                    )
                )
    return event


def next_steps(registry: Registry, ref: str) -> list[Gate]:
    """Gates for every move allowed from the current status (approval checks assume a
    hypothetical independent approver, so they show the other blockers)."""
    asset = registry.get(ref)
    gates = []
    for target in ALLOWED[asset.status]:
        gate = check_transition(
            registry,
            ref,
            target,
            approver="<independent approver>",
            limits="<limits>",
            note="<note>",
        )
        gates.append(gate)
    return gates
