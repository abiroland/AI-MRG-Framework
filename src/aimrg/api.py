"""REST API for AI-MRG (FastAPI).

Run with ``aimrg serve`` (needs ``pip install "ai-mrg[api]"``). Interactive docs are at
``/docs``.

Security model
--------------
* Every request needs ``Authorization: Bearer <token>``. Tokens are issued with
  ``aimrg user add`` and stored only as hashes.
* The authenticated user is always the **actor** recorded on evidence, findings and
  lifecycle events; clients cannot act as someone else.
* Roles gate each operation (see :class:`aimrg.registry.accounts.Role`). Approval
  decisions additionally go through the workflow's independence rules, with the
  authenticated user as the approver.
* User and webhook administration is CLI-only.
"""

# No `from __future__ import annotations`: FastAPI must resolve the dependency
# aliases defined inside create_app() at runtime.

import io
import json
from collections.abc import Callable, Iterator
from datetime import date
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile

from aimrg import __version__
from aimrg.checks import run_control_checks
from aimrg.core.classify import classify
from aimrg.core.models import (
    AICategory,
    AssetRecord,
    EvidenceItem,
    EvidenceType,
    Finding,
    FindingStatus,
    LifecycleEvent,
    LifecycleStatus,
    Severity,
    TierOverride,
)
from aimrg.export import package_bytes
from aimrg.findings import aging, default_due_date
from aimrg.packs.models import ControlStage
from aimrg.packs.resolver import ResolverError, coverage
from aimrg.registry import Registry, RegistryError
from aimrg.registry.accounts import Role, User
from aimrg.reports import markdown_to_docx, render
from aimrg.tiering import assess_asset
from aimrg.workflow import WorkflowError, next_steps, transition

_APPROVALS = {
    LifecycleStatus.APPROVED,
    LifecycleStatus.APPROVED_WITH_LIMITS,
    LifecycleStatus.REJECTED,
}


# --- request bodies -------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetIn(_Body):
    name: str
    owner: str
    business_uses: list[str] = Field(min_length=1)
    ai_category: AICategory
    description: str = ""
    vendor: str | None = None
    depends_on: list[str] = Field(default_factory=list, description="Upstream names or ids.")


class ClassificationIn(_Body):
    is_model_sr26_2: bool
    consequential_decision: bool = False
    third_party: bool = False
    jurisdictions: list[str] = Field(default_factory=list)
    rationale: str


class RiskAssessmentIn(_Body):
    factor_scores: dict[str, int]
    profile_id: str = "default"
    override: TierOverride | None = None


class FindingIn(_Body):
    title: str
    severity: Severity
    control_id: str | None = None
    description: str = ""
    owner: str | None = None
    due_date: date | None = None


class FindingCloseIn(_Body):
    evidence_ids: list[str] = Field(min_length=1)


class TransitionIn(_Body):
    status: LifecycleStatus
    limits: str | None = None
    note: str = ""


class ControlStatusOut(BaseModel):
    control_id: str
    stage: str
    title: str
    status: str
    latest_outcome: str | None
    missing: list[str]
    checks: list[str]


class GateOut(BaseModel):
    target: str
    allowed: bool
    blockers: list[str]


class StatusOut(BaseModel):
    asset: str
    status: str
    history: list[LifecycleEvent]
    next_steps: list[GateOut]


class CheckRunOut(BaseModel):
    check_id: str
    outcome: str
    summary: str
    evidence_id: str | None
    finding_id: str | None
    notes: list[str]


class AuditOut(BaseModel):
    ok: bool
    assets_checked: int
    evidence_checked: int
    problems: list[str]


# --- app ------------------------------------------------------------------------------


def create_app(db: str) -> FastAPI:
    """Create the API for the registry at ``db`` (SQLite path or PostgreSQL URL)."""
    app = FastAPI(
        title="AI-MRG",
        version=__version__,
        description=(
            "Open-source AI and model risk governance. Not legal advice; using AI-MRG does "
            "not by itself establish regulatory compliance."
        ),
    )
    app.state.db = db

    def get_registry(request: Request) -> Iterator[Registry]:
        try:
            registry = Registry.open(request.app.state.db)
        except RegistryError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        try:
            yield registry
        finally:
            registry.close()

    RegistryDep = Annotated[Registry, Depends(get_registry)]

    def current_user(
        registry: RegistryDep, authorization: Annotated[str | None, Header()] = None
    ) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = registry.user_for_token(authorization.split(" ", 1)[1].strip())
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid or disabled token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    UserDep = Annotated[User, Depends(current_user)]

    def require(*roles: Role) -> Callable[[User], User]:
        def check(user: UserDep) -> User:
            if not user.has(*roles):
                needed = ", ".join(r.value for r in roles)
                raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {needed}")
            return user

        return check

    def fail(exc: Exception) -> HTTPException:
        message = str(exc)
        if isinstance(exc, WorkflowError):
            return HTTPException(
                status.HTTP_409_CONFLICT,
                {"message": message.split("\n")[0], "blockers": exc.blockers},
            )
        if message.startswith(("no asset", "no unique", "no user", "no template")):
            return HTTPException(status.HTTP_404_NOT_FOUND, message)
        return HTTPException(status.HTTP_400_BAD_REQUEST, message)

    # --- meta ---------------------------------------------------------------------

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/me", tags=["meta"])
    def me(user: UserDep, registry: RegistryDep) -> dict[str, Any]:
        return {
            "name": user.name,
            "roles": [r.value for r in user.roles],
            "pack": registry.pack.manifest.ref,
            "backend": registry.backend,
        }

    # --- assets -------------------------------------------------------------------

    @app.get("/assets", tags=["assets"])
    def list_assets(_: UserDep, registry: RegistryDep) -> list[AssetRecord]:
        return registry.assets()

    @app.post("/assets", status_code=201, tags=["assets"])
    def create_asset(
        body: AssetIn,
        registry: RegistryDep,
        _: Annotated[User, Depends(require(Role.DEVELOPER, Role.VALIDATOR))],
    ) -> AssetRecord:
        try:
            upstream = [registry.get(ref).id for ref in body.depends_on]
            return registry.register(
                AssetRecord(**body.model_dump(exclude={"depends_on"}), depends_on=upstream)
            )
        except RegistryError as exc:
            raise fail(exc) from exc

    @app.get("/assets/{ref}", tags=["assets"])
    def get_asset(ref: str, _: UserDep, registry: RegistryDep) -> AssetRecord:
        try:
            return registry.get(ref)
        except RegistryError as exc:
            raise fail(exc) from exc

    @app.put("/assets/{ref}/classification", tags=["assets"])
    def put_classification(
        ref: str,
        body: ClassificationIn,
        registry: RegistryDep,
        _: Annotated[User, Depends(require(Role.VALIDATOR))],
    ) -> AssetRecord:
        try:
            asset = registry.get(ref)
            return registry.set_classification(
                asset.id, classify(ai_category=asset.ai_category, **body.model_dump())
            )
        except (RegistryError, ValueError) as exc:
            raise fail(exc) from exc

    @app.put("/assets/{ref}/risk-assessment", tags=["assets"])
    def put_risk_assessment(
        ref: str,
        body: RiskAssessmentIn,
        registry: RegistryDep,
        user: Annotated[User, Depends(require(Role.VALIDATOR))],
    ) -> AssetRecord:
        try:
            return assess_asset(
                registry,
                ref,
                body.factor_scores,
                assessed_by=user.name,
                profile_id=body.profile_id,
                override=body.override,
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown profile {exc}") from exc
        except (RegistryError, ValueError) as exc:
            raise fail(exc) from exc

    @app.get("/assets/{ref}/plan", tags=["assets"])
    def plan(
        ref: str, _: UserDep, registry: RegistryDep, stage: ControlStage | None = None
    ) -> list[ControlStatusOut]:
        try:
            asset = registry.get(ref)
            items = coverage(registry.pack, asset, registry.evidence(asset.id), stage)
        except (RegistryError, ResolverError) as exc:
            raise fail(exc) from exc
        return [
            ControlStatusOut(
                control_id=i.control.id,
                stage=i.control.lifecycle_stage.value,
                title=i.control.title,
                status=i.status.value,
                latest_outcome=i.latest_outcome.value if i.latest_outcome else None,
                missing=[t.value for t in i.missing],
                checks=[c.id for c in i.control.checks],
            )
            for i in items
        ]

    # --- evidence -----------------------------------------------------------------

    @app.get("/assets/{ref}/evidence", tags=["evidence"])
    def list_evidence(
        ref: str, _: UserDep, registry: RegistryDep, control_id: str | None = None
    ) -> list[EvidenceItem]:
        try:
            return registry.evidence(ref, control_id)
        except RegistryError as exc:
            raise fail(exc) from exc

    @app.post("/assets/{ref}/evidence", status_code=201, tags=["evidence"])
    async def add_evidence(
        ref: str,
        request: Request,
        registry: RegistryDep,
        user: Annotated[User, Depends(require(Role.DEVELOPER, Role.VALIDATOR))],
    ) -> EvidenceItem:
        """Multipart form: ``control_id``, ``type`` (document | attestation),
        ``summary``, and an optional ``file``. Approvals are recorded by transitions."""
        async with request.form() as form:
            return await _add_evidence(ref, form, registry, user)

    async def _add_evidence(ref: str, form: Any, registry: Registry, user: User) -> EvidenceItem:
        kind = str(form.get("type", ""))
        if kind not in (EvidenceType.DOCUMENT.value, EvidenceType.ATTESTATION.value):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "type must be 'document' or 'attestation'"
            )
        uri = digest = None
        upload = form.get("file")
        if isinstance(upload, UploadFile):
            suffix = (
                "." + upload.filename.rsplit(".", 1)[-1]
                if upload.filename and "." in upload.filename
                else ""
            )
            uri, digest = registry.store_artifact(await upload.read(), suffix=suffix)
        try:
            return registry.add_evidence(
                EvidenceItem(
                    asset_id=ref,
                    control_id=str(form.get("control_id", "")),
                    type=EvidenceType(kind),
                    summary=str(form.get("summary", "")),
                    payload_uri=uri,
                    payload_sha256=digest,
                    pack_version=registry.pack.manifest.ref,
                    actor=user.name,
                )
            )
        except (RegistryError, ValueError) as exc:
            raise fail(exc) from exc

    # --- checks -------------------------------------------------------------------

    @app.post("/assets/{ref}/checks/{control_id}", tags=["checks"])
    async def run_checks(
        ref: str,
        control_id: str,
        request: Request,
        registry: RegistryDep,
        user: Annotated[User, Depends(require(Role.DEVELOPER, Role.VALIDATOR))],
    ) -> list[CheckRunOut]:
        """Multipart form: one CSV file per dataset role (field name = role, e.g. ``dev``,
        ``oot``, ``baseline``, ``current``, ``decisions``), plus optional ``target``,
        ``score``, ``features`` (comma-separated) and ``params`` (a JSON object)."""
        async with request.form() as form:
            return await _run_checks(ref, control_id, form, registry, user)

    async def _run_checks(
        ref: str, control_id: str, form: Any, registry: Registry, user: User
    ) -> list[CheckRunOut]:
        datasets: dict[str, pd.DataFrame] = {}
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                try:
                    datasets[key] = pd.read_csv(io.BytesIO(await value.read()))
                except (ValueError, pd.errors.ParserError) as exc:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, f"cannot read dataset {key!r}: {exc}"
                    ) from exc
        if not datasets:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "upload at least one dataset")
        try:
            params = json.loads(str(form.get("params") or "{}"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "params must be JSON") from exc
        features = [f.strip() for f in str(form.get("features") or "").split(",") if f.strip()]
        try:
            runs = run_control_checks(
                registry,
                ref,
                control_id,
                datasets,
                actor=user.name,
                target=str(form["target"]) if form.get("target") else None,
                score=str(form["score"]) if form.get("score") else None,
                features=features or None,
                params=params,
            )
        except RegistryError as exc:
            raise fail(exc) from exc
        return [
            CheckRunOut(
                check_id=r.check_id,
                outcome=r.result.outcome.value,
                summary=r.result.summary,
                evidence_id=r.evidence.id if r.evidence else None,
                finding_id=r.finding.id if r.finding else None,
                notes=r.result.notes,
            )
            for r in runs
        ]

    # --- findings -----------------------------------------------------------------

    @app.get("/findings", tags=["findings"])
    def list_findings(
        _: UserDep, registry: RegistryDep, asset: str | None = None, open_only: bool = True
    ) -> list[Finding]:
        try:
            items = registry.findings(asset)
        except RegistryError as exc:
            raise fail(exc) from exc
        if open_only:
            items = [
                f for f in items if f.status in (FindingStatus.OPEN, FindingStatus.IN_REMEDIATION)
            ]
        return items

    @app.get("/findings/aging", tags=["findings"])
    def findings_aging(_: UserDep, registry: RegistryDep) -> list[dict[str, Any]]:
        return [
            {
                "finding": r.finding.model_dump(mode="json"),
                "days_open": r.days_open,
                "days_overdue": r.days_overdue,
                "bucket": r.bucket,
            }
            for r in aging(registry.findings())
        ]

    @app.post("/assets/{ref}/findings", status_code=201, tags=["findings"])
    def open_finding(
        ref: str,
        body: FindingIn,
        registry: RegistryDep,
        _: Annotated[User, Depends(require(Role.VALIDATOR))],
    ) -> Finding:
        try:
            asset = registry.get(ref)
            return registry.add_finding(
                Finding(
                    asset_id=asset.id,
                    control_id=body.control_id,
                    severity=body.severity,
                    title=body.title,
                    description=body.description,
                    owner=body.owner or asset.owner,
                    due_date=body.due_date or default_due_date(body.severity),
                )
            )
        except RegistryError as exc:
            raise fail(exc) from exc

    @app.post("/findings/{finding_id}/close", tags=["findings"])
    def close_finding(
        finding_id: str,
        body: FindingCloseIn,
        registry: RegistryDep,
        _: Annotated[User, Depends(require(Role.VALIDATOR))],
    ) -> Finding:
        try:
            finding = registry.get_finding(finding_id)
            known = {e.id for e in registry.evidence(finding.asset_id)}
            unknown = [e for e in body.evidence_ids if e not in known]
            if unknown:
                raise RegistryError(f"evidence not found for this asset: {unknown}")
            return registry.save_finding(
                finding.model_copy(
                    update={
                        "status": FindingStatus.CLOSED,
                        "remediation_evidence": body.evidence_ids,
                    }
                )
            )
        except RegistryError as exc:
            raise fail(exc) from exc

    @app.post("/findings/{finding_id}/accept", tags=["findings"])
    def accept_finding(
        finding_id: str,
        registry: RegistryDep,
        user: Annotated[User, Depends(require(Role.APPROVER))],
    ) -> Finding:
        try:
            finding = registry.get_finding(finding_id)
            return registry.save_finding(
                finding.model_copy(
                    update={"status": FindingStatus.RISK_ACCEPTED, "risk_acceptance_by": user.name}
                )
            )
        except RegistryError as exc:
            raise fail(exc) from exc

    # --- lifecycle ----------------------------------------------------------------

    @app.get("/assets/{ref}/status", tags=["lifecycle"])
    def get_status(ref: str, _: UserDep, registry: RegistryDep) -> StatusOut:
        try:
            asset = registry.get(ref)
            gates = next_steps(registry, asset.id)
            history = registry.events(asset.id)
        except RegistryError as exc:
            raise fail(exc) from exc
        return StatusOut(
            asset=asset.name,
            status=asset.status.value,
            history=history,
            next_steps=[
                GateOut(target=g.target.value, allowed=g.allowed, blockers=g.blockers)
                for g in gates
            ],
        )

    @app.post("/assets/{ref}/transitions", status_code=201, tags=["lifecycle"])
    def post_transition(
        ref: str, body: TransitionIn, registry: RegistryDep, user: UserDep
    ) -> LifecycleEvent:
        """Approval decisions need the approver role; the caller is the approver.
        Other moves need the developer or validator role."""
        if body.status in _APPROVALS:
            if not user.has(Role.APPROVER):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "requires role: approver")
            approver: str | None = user.name
        else:
            if not user.has(Role.DEVELOPER, Role.VALIDATOR):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "requires role: developer, validator"
                )
            approver = None
        try:
            return transition(
                registry,
                ref,
                body.status,
                actor=user.name,
                approver=approver,
                limits=body.limits,
                note=body.note,
            )
        except RegistryError as exc:
            raise fail(exc) from exc

    # --- reports and audit ----------------------------------------------------------

    @app.get("/reports/{template}", tags=["reports"])
    def portfolio_report(
        template: str, _: UserDep, registry: RegistryDep, format: Literal["md", "docx"] = "md"
    ) -> Response:
        return _report(registry, template, None, format)

    @app.get("/assets/{ref}/reports/{template}", tags=["reports"])
    def asset_report(
        ref: str,
        template: str,
        _: UserDep,
        registry: RegistryDep,
        format: Literal["md", "docx"] = "md",
    ) -> Response:
        return _report(registry, template, ref, format)

    def _report(registry: Registry, template: str, ref: str | None, fmt: str) -> Response:
        try:
            markdown = render(registry, template, ref)
        except RegistryError as exc:
            raise fail(exc) from exc
        if fmt == "md":
            return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = markdown_to_docx(markdown, Path(tmp) / "report.docx", title=template)
            content = path.read_bytes()
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{template}.docx"'},
        )

    @app.get("/export", tags=["audit"])
    def export(
        registry: RegistryDep,
        _: Annotated[User, Depends(require(Role.AUDITOR))],
        asset: Annotated[list[str] | None, Query()] = None,
    ) -> Response:
        """Download an examination package (zip) for the given assets (default: all)."""
        try:
            content, summary = package_bytes(registry, asset)
        except RegistryError as exc:
            raise fail(exc) from exc
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="aimrg-examination-package.zip"',
                "X-AIMRG-Sums-SHA256": summary.sums_sha256,
            },
        )

    @app.get("/audit/verify", tags=["audit"])
    def audit_verify(
        registry: RegistryDep, _: Annotated[User, Depends(require(Role.AUDITOR))]
    ) -> AuditOut:
        result = registry.verify()
        return AuditOut(
            ok=result.ok,
            assets_checked=result.assets_checked,
            evidence_checked=result.evidence_checked,
            problems=result.problems,
        )

    return app
