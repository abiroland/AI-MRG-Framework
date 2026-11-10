"""The ``aimrg`` command-line interface."""

from __future__ import annotations

import getpass
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import click
import pandas as pd

from aimrg import __version__
from aimrg.adverse_action import derive_reasons, load_reason_map, reason_texts
from aimrg.checks import available_checks, get_check, run_control_checks
from aimrg.core.classify import QUESTIONS, classify
from aimrg.core.models import (
    AICategory,
    AssetRecord,
    CheckOutcome,
    EvidenceItem,
    EvidenceType,
    Finding,
    FindingStatus,
    LifecycleStatus,
    Severity,
    TierOverride,
)
from aimrg.findings import aging, default_due_date
from aimrg.packs.loader import (
    PackValidationError,
    builtin_pack_names,
    builtin_packs_dir,
    load_pack,
)
from aimrg.packs.models import ControlStage
from aimrg.packs.resolver import ResolverError, coverage
from aimrg.registry import Registry, RegistryError, import_assets
from aimrg.registry.accounts import WEBHOOK_EVENTS, Role, Webhook, deliver
from aimrg.reports import available_templates, markdown_to_docx, render
from aimrg.schema import export_schemas, stale_schemas
from aimrg.tiering import assess_asset
from aimrg.workflow import WorkflowError, next_steps, transition

_OUTCOME_COLORS = {
    "pass": "green",
    "complete": "green",
    "warn": "yellow",
    "partial": "yellow",
    "fail": "red",
    "failing": "red",
    "error": "red",
    "missing": "red",
}


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    text_rows = [["—" if v is None else str(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in text_rows:
        widths = [max(w, len(v)) for w, v in zip(widths, row, strict=True)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    click.echo("  ".join("-" * w for w in widths))
    for row in text_rows:
        cells = []
        for value, width in zip(row, widths, strict=True):
            padded = value.ljust(width)
            color = _OUTCOME_COLORS.get(value.split(" ")[0])
            cells.append(click.style(padded, fg=color) if color else padded)
        click.echo("  ".join(cells))


class _State:
    def __init__(self, db: str, actor: str) -> None:
        self.db = db
        self.actor = actor

    def registry(self) -> Registry:
        try:
            return Registry.open(self.db)
        except RegistryError as exc:
            raise click.ClickException(str(exc)) from exc


pass_state = click.make_pass_decorator(_State)


def _fail(exc: Exception) -> click.ClickException:
    return click.ClickException(str(exc))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="aimrg")
@click.option(
    "--db",
    default="aimrg.db",
    envvar="AIMRG_DB",
    show_default=True,
    help="Registry: a SQLite file or a postgresql:// URL (or set AIMRG_DB).",
)
@click.option(
    "--actor",
    default=lambda: getpass.getuser(),
    envvar="AIMRG_ACTOR",
    help="Who is acting; recorded on evidence (or set AIMRG_ACTOR). Defaults to your login.",
)
@click.pass_context
def main(ctx: click.Context, db: str, actor: str) -> None:
    """AI-MRG: open-source AI and model risk governance toolkit.

    Not legal advice. Using AI-MRG does not by itself establish compliance.
    """
    ctx.obj = _State(db, actor)


# --- registry setup -----------------------------------------------------------


@main.command()
@click.option("--pack", default="fin-us-banking", show_default=True, help="Pack name or path.")
@click.option(
    "--artifacts-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where evidence artifacts are stored (required for PostgreSQL; defaults to "
    "<db>-artifacts next to a SQLite file).",
)
@pass_state
def init(state: _State, pack: str, artifacts_dir: Path | None) -> None:
    """Create a new registry."""
    try:
        registry = Registry.create(state.db, pack=pack, artifacts_dir=artifacts_dir)
    except (RegistryError, PackValidationError) as exc:
        raise _fail(exc) from exc
    where = "PostgreSQL registry" if registry.backend == "postgresql" else state.db
    click.secho(f"Created {where} using {registry.pack.manifest.ref}", fg="green")
    if not registry.pack.manifest.reviewed:
        click.secho(
            "Note: this pack's content is a draft, not yet practitioner-reviewed.", fg="yellow"
        )
    registry.close()


@main.command()
@click.argument("name")
@click.option("--owner", required=True)
@click.option("--use", "uses", multiple=True, required=True, help="Business use tag (repeatable).")
@click.option(
    "--category",
    type=click.Choice([c.value for c in AICategory]),
    required=True,
    help="Technique category.",
)
@click.option("--description", default="")
@click.option("--vendor", default=None, help="Vendor name, if third-party.")
@click.option("--depends-on", multiple=True, help="Upstream asset name or id (repeatable).")
@pass_state
def register(
    state: _State,
    name: str,
    owner: str,
    uses: tuple[str, ...],
    category: str,
    description: str,
    vendor: str | None,
    depends_on: tuple[str, ...],
) -> None:
    """Register a model or AI system."""
    with state.registry() as registry:
        try:
            upstream = [registry.get(ref).id for ref in depends_on]
            asset = registry.register(
                AssetRecord(
                    name=name,
                    owner=owner,
                    business_uses=list(uses),
                    ai_category=AICategory(category),
                    description=description,
                    vendor=vendor,
                    depends_on=upstream,
                )
            )
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Registered {asset.name} ({asset.id[:12]})", fg="green")
    click.echo(f"Next: aimrg classify {_quote(asset.name)}")


@main.command(name="import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@pass_state
def import_(state: _State, file: Path) -> None:
    """Bulk-import assets from a CSV or YAML file (all-or-nothing)."""
    with state.registry() as registry:
        try:
            created = import_assets(registry, file)
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Imported {len(created)} asset(s).", fg="green")


@main.command(name="list")
@pass_state
def list_(state: _State) -> None:
    """List registered assets."""
    with state.registry() as registry:
        rows = []
        for a in registry.assets():
            open_findings = sum(
                f.status in (FindingStatus.OPEN, FindingStatus.IN_REMEDIATION)
                for f in registry.findings(a.id)
            )
            rows.append(
                [
                    a.name,
                    a.owner,
                    a.classification.track.value if a.classification else None,
                    a.risk_assessment.tier if a.risk_assessment else None,
                    a.status.value,
                    open_findings,
                ]
            )
    if not rows:
        click.echo("No assets registered. Add one with `aimrg register` or `aimrg import`.")
        return
    _table(["Asset", "Owner", "Track", "Tier", "Status", "Open findings"], rows)


@main.command()
@click.argument("asset")
@pass_state
def show(state: _State, asset: str) -> None:
    """Show an asset's record as YAML-like text."""
    with state.registry() as registry:
        try:
            record = registry.get(asset)
        except RegistryError as exc:
            raise _fail(exc) from exc
    import yaml

    click.echo(yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False))


# --- classification and tiering -------------------------------------------------


@main.command(name="classify")
@click.argument("asset")
@click.option(
    "--model/--not-model", "is_model", default=None, help="Meets SR 26-2 model definition."
)
@click.option("--consequential/--not-consequential", default=None)
@click.option("--third-party/--in-house", "third_party", default=None)
@click.option("--jurisdiction", "jurisdictions", multiple=True, help="e.g. US, US-CO (repeatable).")
@click.option("--rationale", default=None)
@pass_state
def classify_cmd(
    state: _State,
    asset: str,
    is_model: bool | None,
    consequential: bool | None,
    third_party: bool | None,
    jurisdictions: tuple[str, ...],
    rationale: str | None,
) -> None:
    """Classify an asset and assign its governance track (prompts for missing answers)."""
    help_text = {q.key: q for q in QUESTIONS}

    def ask_bool(key: str, current: bool | None) -> bool:
        if current is not None:
            return current
        q = help_text[key]
        click.secho(q.help, dim=True)
        return click.confirm(q.prompt)

    with state.registry() as registry:
        try:
            record = registry.get(asset)
            answers_model = ask_bool("is_model_sr26_2", is_model)
            answers_cons = ask_bool("consequential_decision", consequential)
            default_third = record.vendor is not None
            answers_third = ask_bool(
                "third_party",
                third_party if third_party is not None else (True if default_third else None),
            )
            juris = (
                list(jurisdictions)
                or click.prompt(help_text["jurisdictions"].prompt, default="US")
                .replace(",", " ")
                .split()
            )
            reason = rationale or click.prompt(help_text["rationale"].prompt)
            result = classify(
                ai_category=record.ai_category,
                is_model_sr26_2=answers_model,
                consequential_decision=answers_cons,
                third_party=answers_third,
                jurisdictions=juris,
                rationale=reason,
            )
            registry.set_classification(record.id, result)
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"{record.name}: track = {result.track.value}", fg="green")
    if result.track.value == "ai_system":
        click.echo("Generative/agentic AI is outside SR 26-2; AI-system track controls apply.")
    click.echo(f"Next: aimrg tier {_quote(record.name)}")


def _parse_scores(values: tuple[str, ...]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for value in values:
        match = re.fullmatch(r"\s*([a-z_][a-z0-9_]*)\s*=\s*(\d+)\s*", value)
        if not match:
            raise click.BadParameter(
                f"expected factor=integer, got {value!r}", param_hint="--score"
            )
        scores[match.group(1)] = int(match.group(2))
    return scores


@main.command()
@click.argument("asset")
@click.option("--score", "scores", multiple=True, help="factor=value (repeatable).")
@click.option("--profile", default="default", show_default=True)
@click.option("--override-tier", type=click.IntRange(1, 4), default=None)
@click.option("--justification", default=None, help="Required with --override-tier.")
@click.option("--approved-by", default=None, help="Required with --override-tier.")
@pass_state
def tier(
    state: _State,
    asset: str,
    scores: tuple[str, ...],
    profile: str,
    override_tier: int | None,
    justification: str | None,
    approved_by: str | None,
) -> None:
    """Assess an asset's risk tier (prompts for any factor not given with --score)."""
    given = _parse_scores(scores)
    with state.registry() as registry:
        try:
            prof = registry.pack.tiering_profile(profile)
        except KeyError as exc:
            raise click.ClickException(f"no tiering profile {profile!r}") from exc
        for factor in prof.factors:
            if factor.id not in given:
                click.secho(f"\n{factor.id}: {factor.description}", bold=True)
                for level, meaning in sorted(factor.guide.items()):
                    click.secho(f"  {level} = {meaning}", dim=True)
                given[factor.id] = click.prompt(
                    "  score",
                    type=click.IntRange(factor.scale_min, factor.scale_max),
                )
        override = None
        if override_tier is not None:
            if not justification or not approved_by:
                raise click.UsageError("--override-tier needs --justification and --approved-by")
            override = TierOverride(
                tier=override_tier, justification=justification, approved_by=approved_by
            )
        try:
            record = assess_asset(
                registry,
                asset,
                given,
                assessed_by=state.actor,
                profile_id=profile,
                override=override,
            )
        except (RegistryError, ValueError) as exc:
            raise _fail(exc) from exc
    ra = record.risk_assessment
    assert ra is not None
    click.secho(
        f"{record.name}: score {ra.computed_score:.3f} → tier {ra.computed_tier}"
        + (f" (override → tier {ra.tier})" if ra.override else ""),
        fg="green",
    )
    for flag in ra.aggregate_flags:
        click.echo(f"  aggregate risk: {flag}")
    click.echo(f"Next: aimrg plan {_quote(record.name)}")


# --- planning and evidence ------------------------------------------------------


@main.command()
@click.argument("asset")
@click.option("--stage", type=click.Choice([s.value for s in ControlStage]), default=None)
@pass_state
def plan(state: _State, asset: str, stage: str | None) -> None:
    """Show the controls that apply to an asset and how far evidence covers them."""
    with state.registry() as registry:
        try:
            record = registry.get(asset)
            items = coverage(registry.pack, record, registry.evidence(record.id), stage)
        except (RegistryError, ResolverError) as exc:
            raise _fail(exc) from exc
    rows = []
    for item in items:
        checks = ", ".join(
            c.id + ("" if get_check(c.id) else " (n/a)") for c in item.control.checks
        )
        status = item.status.value + (
            f" ({item.latest_outcome.value})" if item.latest_outcome else ""
        )
        rows.append(
            [
                item.control.id,
                item.control.lifecycle_stage.value,
                status,
                ", ".join(t.value for t in item.missing) or None,
                checks or None,
                item.control.title,
            ]
        )
    _table(["Control", "Stage", "Status", "Missing", "Checks", "Title"], rows)
    counts = Counter(i.status.value for i in items)
    click.echo(
        f"\n{len(items)} control(s): " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    )


@main.group()
def evidence() -> None:
    """Record and list evidence."""


@evidence.command("add")
@click.argument("asset")
@click.argument("control")
@click.option(
    "--type",
    "type_",
    type=click.Choice([t.value for t in EvidenceType if t is not EvidenceType.CHECK_RESULT]),
    required=True,
    help="check_result evidence is recorded by `aimrg check run`.",
)
@click.option("--summary", required=True)
@click.option("--file", "file_", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@pass_state
def evidence_add(
    state: _State, asset: str, control: str, type_: str, summary: str, file_: Path | None
) -> None:
    """Record a document, attestation or approval against a control."""
    with state.registry() as registry:
        uri = digest = None
        if file_ is not None:
            uri, digest = registry.store_artifact(file_.read_bytes(), suffix=file_.suffix)
        try:
            item = registry.add_evidence(
                EvidenceItem(
                    asset_id=asset,
                    control_id=control,
                    type=EvidenceType(type_),
                    summary=summary,
                    payload_uri=uri,
                    payload_sha256=digest,
                    pack_version=registry.pack.manifest.ref,
                    actor=state.actor,
                )
            )
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Recorded {item.type.value} for {control} ({item.id[:12]})", fg="green")


@evidence.command("list")
@click.argument("asset")
@click.option("--control", default=None)
@pass_state
def evidence_list(state: _State, asset: str, control: str | None) -> None:
    """List evidence recorded for an asset."""
    with state.registry() as registry:
        try:
            items = registry.evidence(asset, control)
        except RegistryError as exc:
            raise _fail(exc) from exc
    rows = [
        [
            e.id[:12],
            e.control_id,
            e.type.value,
            e.outcome.value if e.outcome else None,
            e.actor,
            e.recorded_at.strftime("%Y-%m-%d %H:%M"),
            (e.summary[:70] + "…") if len(e.summary) > 70 else e.summary,
        ]
        for e in items
    ]
    if not rows:
        click.echo("No evidence recorded.")
        return
    _table(["Id", "Control", "Type", "Outcome", "Actor", "Recorded", "Summary"], rows)


# --- checks ---------------------------------------------------------------------


@main.group(name="check")
def check_group() -> None:
    """Run automated checks."""


@check_group.command("list")
def check_list() -> None:
    """List the checks available in this installation."""
    for check_id in available_checks():
        fn = get_check(check_id)
        doc = (fn.__doc__ or "").strip().splitlines()[0] if fn and fn.__doc__ else ""
        click.echo(f"{check_id:<36} {doc}")


def _read_dataset(spec: str) -> tuple[str, pd.DataFrame]:
    if "=" not in spec:
        raise click.BadParameter(f"expected role=path, got {spec!r}", param_hint="--data")
    role, raw_path = spec.split("=", 1)
    path = Path(raw_path)
    if not path.is_file():
        raise click.BadParameter(f"file not found: {path}", param_hint="--data")
    if path.suffix.lower() == ".parquet":
        return role.strip(), pd.read_parquet(path)
    return role.strip(), pd.read_csv(path)


@check_group.command("run")
@click.argument("asset")
@click.argument("control")
@click.option(
    "--data",
    "data_specs",
    multiple=True,
    required=True,
    help="role=path.csv, e.g. dev=dev.csv oot=oot.csv (roles: dev, test, oot, baseline, "
    "current, population).",
)
@click.option("--target", default=None, help="Observed outcome column (1 = event).")
@click.option("--score", default=None, help="Model score column (higher = riskier).")
@click.option("--feature", "features", multiple=True, help="Feature column (repeatable).")
@click.option(
    "--param",
    "param_specs",
    multiple=True,
    help="Check parameter key=value (repeatable), e.g. control_group=group_a, "
    "group_col=group, reason_map=reasons.yaml.",
)
@click.option("--no-findings", is_flag=True, help="Do not open findings for failures.")
@pass_state
def check_run(
    state: _State,
    asset: str,
    control: str,
    data_specs: tuple[str, ...],
    target: str | None,
    score: str | None,
    features: tuple[str, ...],
    param_specs: tuple[str, ...],
    no_findings: bool,
) -> None:
    """Run a control's automated checks and record the results as evidence."""
    datasets = dict(_read_dataset(spec) for spec in data_specs)
    params = _parse_params(param_specs)
    with state.registry() as registry:
        try:
            runs = run_control_checks(
                registry,
                asset,
                control,
                datasets,
                actor=state.actor,
                target=target,
                score=score,
                features=list(features) or None,
                params=params,
                open_findings=not no_findings,
            )
        except RegistryError as exc:
            raise _fail(exc) from exc
    for run in runs:
        outcome = run.result.outcome.value
        click.echo(
            click.style(f"[{outcome.upper():5}]", fg=_OUTCOME_COLORS.get(outcome))
            + f" {run.check_id}: {run.result.summary}"
        )
        for note in run.result.notes:
            click.echo(f"        {note}")
        if run.evidence is None:
            click.echo("        (not recorded)")
        if run.finding is not None:
            click.secho(
                f"        finding opened: {run.finding.id[:8]} {run.finding.title}", fg="red"
            )
    if any(r.result.outcome is CheckOutcome.FAIL for r in runs):
        raise SystemExit(2)


# --- findings -------------------------------------------------------------------


@main.group()
def finding() -> None:
    """Open, list and close findings."""


@finding.command("list")
@click.argument("asset", required=False)
@click.option("--all", "show_all", is_flag=True, help="Include closed and risk-accepted.")
@pass_state
def finding_list(state: _State, asset: str | None, show_all: bool) -> None:
    """List findings (open ones by default)."""
    with state.registry() as registry:
        try:
            items = registry.findings(asset)
            names = {a.id: a.name for a in registry.assets()}
        except RegistryError as exc:
            raise _fail(exc) from exc
    if not show_all:
        items = [f for f in items if f.status in (FindingStatus.OPEN, FindingStatus.IN_REMEDIATION)]
    if not items:
        click.echo("No findings.")
        return
    _table(
        ["Id", "Asset", "Severity", "Status", "Control", "Due", "Title"],
        [
            [
                f.id[:8],
                names.get(f.asset_id),
                f.severity.value,
                f.status.value,
                f.control_id,
                f.due_date,
                f.title,
            ]
            for f in items
        ],
    )


@finding.command("open")
@click.argument("asset")
@click.option("--title", required=True)
@click.option("--severity", type=click.Choice([s.value for s in Severity]), required=True)
@click.option("--owner", default=None, help="Defaults to the asset owner.")
@click.option("--control", default=None)
@click.option("--due", type=click.DateTime(formats=["%Y-%m-%d"]), default=None)
@click.option("--description", default="")
@pass_state
def finding_open(
    state: _State,
    asset: str,
    title: str,
    severity: str,
    owner: str | None,
    control: str | None,
    due: Any,
    description: str,
) -> None:
    """Open a finding manually."""
    with state.registry() as registry:
        try:
            record = registry.get(asset)
            item = registry.add_finding(
                Finding(
                    asset_id=record.id,
                    control_id=control,
                    severity=Severity(severity),
                    title=title,
                    description=description,
                    owner=owner or record.owner,
                    due_date=due.date() if due else default_due_date(Severity(severity)),
                )
            )
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Opened finding {item.id[:8]}", fg="green")


@finding.command("close")
@click.argument("finding_id")
@click.option(
    "--evidence", "evidence_ids", multiple=True, required=True, help="Evidence id (prefix ok)."
)
@pass_state
def finding_close(state: _State, finding_id: str, evidence_ids: tuple[str, ...]) -> None:
    """Close a finding, citing remediation evidence."""
    with state.registry() as registry:
        try:
            item = registry.get_finding(finding_id)
            all_evidence = registry.evidence(item.asset_id)
            resolved = []
            for ref in evidence_ids:
                matches = [e.id for e in all_evidence if e.id.startswith(ref)]
                if len(matches) != 1:
                    raise RegistryError(f"no unique evidence for this asset matching {ref!r}")
                resolved.append(matches[0])
            registry.save_finding(
                item.model_copy(
                    update={"status": FindingStatus.CLOSED, "remediation_evidence": resolved}
                )
            )
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Closed finding {item.id[:8]}", fg="green")


@finding.command("accept")
@click.argument("finding_id")
@click.option("--by", "accepted_by", required=True, help="Who accepted the risk.")
@pass_state
def finding_accept(state: _State, finding_id: str, accepted_by: str) -> None:
    """Record risk acceptance for a finding."""
    with state.registry() as registry:
        try:
            item = registry.get_finding(finding_id)
            registry.save_finding(
                item.model_copy(
                    update={
                        "status": FindingStatus.RISK_ACCEPTED,
                        "risk_acceptance_by": accepted_by,
                    }
                )
            )
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Risk accepted for finding {item.id[:8]} by {accepted_by}", fg="yellow")


@finding.command("aging")
@click.argument("asset", required=False)
@pass_state
def finding_aging(state: _State, asset: str | None) -> None:
    """Open findings by age and how overdue they are."""
    with state.registry() as registry:
        try:
            rows = aging(registry.findings(asset))
            names = {a.id: a.name for a in registry.assets()}
        except RegistryError as exc:
            raise _fail(exc) from exc
    if not rows:
        click.echo("No open findings.")
        return
    _table(
        ["Id", "Asset", "Severity", "Opened", "Due", "Days open", "Overdue", "Bucket", "Title"],
        [
            [
                r.finding.id[:8],
                names.get(r.finding.asset_id),
                r.finding.severity.value,
                r.finding.opened_at.date(),
                r.finding.due_date,
                r.days_open,
                r.days_overdue,
                r.bucket,
                r.finding.title,
            ]
            for r in rows
        ],
    )
    overdue = sum(r.days_overdue > 0 for r in rows)
    click.echo(f"\n{len(rows)} open finding(s), {overdue} overdue.")


# --- lifecycle ------------------------------------------------------------------


@main.command(name="transition")
@click.argument("asset")
@click.argument("status", type=click.Choice([s.value for s in LifecycleStatus]))
@click.option("--approver", default=None, help="Independent approver (approval decisions).")
@click.option("--limits", default=None, help="Use limitations (approved_with_limits).")
@click.option("--note", default="", help="Reason for the change.")
@pass_state
def transition_cmd(
    state: _State, asset: str, status: str, approver: str | None, limits: str | None, note: str
) -> None:
    """Move an asset to a new lifecycle status, if its gate allows."""
    with state.registry() as registry:
        try:
            event = transition(
                registry,
                asset,
                status,
                actor=state.actor,
                approver=approver,
                limits=limits,
                note=note,
            )
        except WorkflowError as exc:
            raise _fail(exc) from exc
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(
        f"{event.from_status.value} → {event.to_status.value}"
        + (f" (approved by {event.approver})" if event.approver else ""),
        fg="green",
    )


@main.command(name="status")
@click.argument("asset")
@pass_state
def status_cmd(state: _State, asset: str) -> None:
    """Show an asset's lifecycle history and what blocks each next step."""
    with state.registry() as registry:
        try:
            record = registry.get(asset)
            history = registry.events(record.id)
            gates = next_steps(registry, record.id)
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"{record.name}: {record.status.value}", bold=True)
    if history:
        click.echo("\nHistory:")
        for e in history:
            extra = f" by {e.actor}" + (f", approved by {e.approver}" if e.approver else "")
            moved = f"{e.from_status.value} → {e.to_status.value}"
            click.echo(f"  {e.occurred_at:%Y-%m-%d %H:%M}  {moved}{extra}")
            if e.limits:
                click.echo(f"      limits: {e.limits}")
            if e.note:
                click.echo(f"      note: {e.note}")
    if not gates:
        click.echo("\nNo further transitions.")
        return
    click.echo("\nNext steps:")
    for gate in gates:
        if gate.allowed:
            click.secho(f"  ✓ {gate.target.value}", fg="green")
        else:
            click.secho(f"  ✗ {gate.target.value}", fg="red")
            for blocker in gate.blockers:
                click.echo(f"      - {blocker}")


# --- users and webhooks ---------------------------------------------------------


@main.group()
def user() -> None:
    """API users and tokens (for `aimrg serve`)."""


@user.command("add")
@click.argument("name")
@click.option(
    "--role",
    "roles",
    multiple=True,
    required=True,
    type=click.Choice([r.value for r in Role]),
    help="Role (repeatable).",
)
@pass_state
def user_add(state: _State, name: str, roles: tuple[str, ...]) -> None:
    """Create an API user and print its token (shown only once)."""
    with state.registry() as registry:
        try:
            created, token = registry.add_user(name, list(roles))
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Created user {created.name} ({', '.join(created.roles)})", fg="green")
    click.echo(f"Token (store it securely; it will not be shown again):\n{token}")


@user.command("list")
@pass_state
def user_list(state: _State) -> None:
    """List API users."""
    with state.registry() as registry:
        users = registry.users()
    if not users:
        click.echo("No users.")
        return
    _table(
        ["Name", "Roles", "Active", "Created"],
        [
            [u.name, ", ".join(u.roles), "yes" if u.active else "no", u.created_at.date()]
            for u in users
        ],
    )


@user.command("disable")
@click.argument("name")
@pass_state
def user_disable(state: _State, name: str) -> None:
    """Disable a user; their token stops working."""
    with state.registry() as registry:
        try:
            registry.set_user_active(name, False)
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho(f"Disabled {name}", fg="yellow")


@user.command("rotate-token")
@click.argument("name")
@pass_state
def user_rotate(state: _State, name: str) -> None:
    """Issue a new token for a user; the old one stops working."""
    with state.registry() as registry:
        try:
            token = registry.rotate_token(name)
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.echo(f"New token for {name} (shown once):\n{token}")


@main.group()
def webhook() -> None:
    """Webhook notifications (findings, failed checks, lifecycle changes)."""


@webhook.command("add")
@click.argument("url")
@click.option(
    "--event",
    "events",
    multiple=True,
    type=click.Choice(["*", *WEBHOOK_EVENTS]),
    help="Event to send (repeatable; default: all).",
)
@pass_state
def webhook_add(state: _State, url: str, events: tuple[str, ...]) -> None:
    """Send signed JSON notifications to URL."""
    try:
        hook = Webhook(url=url, events=list(events) or ["*"])
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    with state.registry() as registry:
        registry.add_webhook(hook)
    click.secho(f"Added webhook {hook.id[:8]} → {hook.url} ({', '.join(hook.events)})", fg="green")
    click.echo(
        "Signing secret (verify the X-AIMRG-Signature header with HMAC-SHA256; shown once):\n"
        f"{hook.secret}"
    )


@webhook.command("list")
@pass_state
def webhook_list(state: _State) -> None:
    """List webhooks."""
    with state.registry() as registry:
        hooks = registry.webhooks()
    if not hooks:
        click.echo("No webhooks.")
        return
    _table(["Id", "URL", "Events"], [[h.id[:8], h.url, ", ".join(h.events)] for h in hooks])


@webhook.command("remove")
@click.argument("webhook_id")
@pass_state
def webhook_remove(state: _State, webhook_id: str) -> None:
    """Remove a webhook (id prefix ok)."""
    with state.registry() as registry:
        try:
            registry.remove_webhook(webhook_id)
        except RegistryError as exc:
            raise _fail(exc) from exc
    click.secho("Removed.", fg="green")


@webhook.command("test")
@click.argument("webhook_id")
@pass_state
def webhook_test(state: _State, webhook_id: str) -> None:
    """Send a test notification."""
    with state.registry() as registry:
        matches = [h for h in registry.webhooks() if h.id.startswith(webhook_id)]
    if len(matches) != 1:
        raise click.ClickException(f"no unique webhook matching {webhook_id!r}")
    result = deliver(matches[0], "test", {"message": "AI-MRG webhook test"})
    if not result.ok:
        raise click.ClickException(f"delivery failed: {result.detail}")
    click.secho(f"Delivered ({result.detail})", fg="green")


# --- API server -----------------------------------------------------------------


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@pass_state
def serve(state: _State, host: str, port: int) -> None:  # pragma: no cover - runs a server
    """Run the REST API (needs: pip install "ai-mrg[api]")."""
    try:
        import uvicorn

        from aimrg.api import create_app
    except ImportError as exc:
        raise click.ClickException('the API needs: pip install "ai-mrg[api]"') from exc
    state.registry().close()  # fail fast if the registry is missing
    uvicorn.run(create_app(state.db), host=host, port=port)


# --- adverse action -------------------------------------------------------------


@main.group(name="reasons")
def reasons_group() -> None:
    """Adverse-action reasons (ECOA / Regulation B)."""


@reasons_group.command("derive")
@click.argument("data", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--map", "map_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--prefix", default="contrib_", show_default=True, help="Attribution column prefix.")
@click.option("--adverse-col", default="declined", show_default=True)
@click.option("--max-reasons", default=4, show_default=True, type=click.IntRange(1, 10))
@click.option("--text", is_flag=True, help="Output reason statements instead of codes.")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
def reasons_derive(
    data: Path,
    map_path: Path,
    prefix: str,
    adverse_col: str,
    max_reasons: int,
    text: bool,
    output: Path,
) -> None:
    """Derive principal reasons for each adverse decision in DATA (a CSV of decisions
    with per-factor attribution columns)."""
    frame = pd.read_csv(data)
    if adverse_col not in frame.columns:
        raise click.ClickException(f"column {adverse_col!r} not found in {data}")
    reason_map = load_reason_map(map_path)
    adverse = frame[frame[adverse_col] == 1]
    try:
        codes = derive_reasons(adverse, reason_map, prefix=prefix, max_reasons=max_reasons)
    except ValueError as exc:
        raise _fail(exc) from exc
    result = reason_texts(codes, reason_map) if text else codes
    result.to_csv(output, index_label="row")
    unmapped = int(
        codes.apply(lambda c: c.astype("string").str.startswith("UNMAPPED:")).sum().sum()
    )
    click.secho(f"wrote reasons for {len(adverse)} adverse decision(s) to {output}", fg="green")
    if unmapped:
        click.secho(f"{unmapped} reason(s) have no statement in the map (UNMAPPED:...)", fg="red")


# --- reports and audit ------------------------------------------------------------


@main.group()
def report() -> None:
    """Generate documents from recorded evidence."""


@report.command("list")
@pass_state
def report_list(state: _State) -> None:
    """List available report templates."""
    with state.registry() as registry:
        for name in available_templates(registry):
            click.echo(name)


@report.command("render")
@click.argument("template")
@click.argument("asset", required=False)
@click.option("--format", "fmt", type=click.Choice(["md", "docx"]), default="md", show_default=True)
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), default=None)
@pass_state
def report_render(
    state: _State, template: str, asset: str | None, fmt: str, output: Path | None
) -> None:
    """Render TEMPLATE (e.g. validation_report ASSET, or inventory_report)."""
    with state.registry() as registry:
        try:
            markdown = render(registry, template, asset)
        except RegistryError as exc:
            raise _fail(exc) from exc
    if fmt == "md":
        if output is None:
            click.echo(markdown)
        else:
            output.write_text(markdown, encoding="utf-8")
            click.secho(f"wrote {output}", fg="green")
        return
    target = output or Path(f"{template}{'-' + _slug(asset) if asset else ''}-{date.today()}.docx")
    markdown_to_docx(markdown, target, title=template.replace("_", " ").title())
    click.secho(f"wrote {target}", fg="green")


@main.group()
def audit() -> None:
    """Audit-trail integrity."""


@audit.command("verify")
@pass_state
def audit_verify(state: _State) -> None:
    """Recompute every evidence hash chain and check stored artifacts."""
    with state.registry() as registry:
        result = registry.verify()
    if result.ok:
        click.secho(
            f"OK: {result.evidence_checked} evidence item(s) across "
            f"{result.assets_checked} asset(s); all chains intact.",
            fg="green",
        )
        return
    click.secho(f"{len(result.problems)} integrity problem(s):", fg="red", err=True)
    for problem in result.problems:
        click.echo(f"  - {problem}", err=True)
    raise SystemExit(1)


# --- packs ----------------------------------------------------------------------


@main.group()
def pack() -> None:
    """Work with content packs."""


@pack.command("list")
def pack_list() -> None:
    """List the packs shipped with AI-MRG."""
    names = builtin_pack_names()
    if not names:
        click.echo("No built-in packs found.")
        return
    for name in names:
        try:
            loaded = load_pack(builtin_packs_dir() / name)
        except PackValidationError:
            click.echo(f"{name}  (invalid; run `aimrg pack validate {name}`)")
            continue
        m = loaded.manifest
        reviewed = "reviewed" if m.reviewed else "NOT YET REVIEWED"
        click.echo(f"{m.ref}  {m.title}  [effective as of {m.effective_as_of}; {reviewed}]")


@pack.command("validate")
@click.argument("target")
def pack_validate(target: str) -> None:
    """Validate a pack. TARGET is a pack directory or the name of a built-in pack."""
    path = Path(target)
    if not path.is_dir() and target in builtin_pack_names():
        path = builtin_packs_dir() / target
    try:
        loaded = load_pack(path)
    except PackValidationError as exc:
        click.secho(str(exc), fg="red", err=True)
        raise SystemExit(1) from exc

    m = loaded.manifest
    stages = Counter(c.lifecycle_stage.value for c in loaded.controls)
    missing_checks = sorted(
        {c.id for control in loaded.controls for c in control.checks} - set(available_checks())
    )
    click.secho(f"OK  {m.ref}  ({m.title})", fg="green")
    click.echo(f"  effective as of : {m.effective_as_of}")
    click.echo(f"  reviewed        : {'yes' if m.reviewed else 'no (draft content)'}")
    click.echo(f"  sources         : {len(loaded.sources)}")
    click.echo(f"  controls        : {len(loaded.controls)}")
    for stage, count in sorted(stages.items()):
        click.echo(f"    {stage:<15} {count}")
    click.echo(f"  tiering profiles: {', '.join(p.id for p in loaded.tiering_profiles)}")
    click.echo(f"  thresholds      : {len(loaded.thresholds)}")
    if missing_checks:
        click.echo(f"  checks not yet implemented: {', '.join(missing_checks)}")


# --- schemas --------------------------------------------------------------------


@main.group()
def schema() -> None:
    """Work with the published JSON Schemas."""


@schema.command("export")
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--check", is_flag=True, help="Only check that OUT_DIR is up to date.")
def schema_export(out_dir: Path, check: bool) -> None:
    """Write JSON Schemas for the domain and pack models to OUT_DIR."""
    if check:
        stale = stale_schemas(out_dir)
        if stale:
            click.secho(f"Out of date in {out_dir}: {', '.join(stale)}", fg="red", err=True)
            click.echo(f"Run: aimrg schema export {out_dir}", err=True)
            raise SystemExit(1)
        click.secho(f"Schemas in {out_dir} are up to date.", fg="green")
        return
    for path in export_schemas(out_dir):
        click.echo(f"wrote {path}")


# --- helpers --------------------------------------------------------------------


def _parse_params(specs: tuple[str, ...]) -> dict[str, Any]:
    import yaml

    params: dict[str, Any] = {}
    for spec in specs:
        if "=" not in spec:
            raise click.BadParameter(f"expected key=value, got {spec!r}", param_hint="--param")
        key, raw = spec.split("=", 1)
        params[key.strip()] = yaml.safe_load(raw) if raw.strip() else ""
    return params


def _quote(name: str) -> str:
    return f'"{name}"' if " " in name else name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


if __name__ == "__main__":  # pragma: no cover
    main()
