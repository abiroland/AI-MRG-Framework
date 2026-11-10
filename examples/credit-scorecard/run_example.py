"""End-to-end AI-MRG walkthrough on a simulated credit-card underwriting model.

Run from anywhere:

    python examples/credit-scorecard/run_example.py [OUTPUT_DIR]

It creates OUTPUT_DIR (default: ./credit-scorecard-demo) containing the scored datasets
as CSV, ``aimrg.db`` (the governance registry), and the generated reports.

The story, step by step:

1. The model is registered, classified and tiered (Tier 1), then moves to development.
2. The developer records data-quality and performance testing; the model moves to
   validation.
3. The validator replicates performance, and runs fair-lending and adverse-action checks.
   A fair-lending screening metric fails and opens a finding, which blocks approval.
4. The fair-lending team documents its review; the finding is closed with that evidence,
   and an independent approver approves the model.
5. Implementation controls (adverse-action notices, Colorado disclosures) are evidenced;
   the model goes into production.
6. A later monitoring cycle finds drift. Checks fail, findings open, and a change request
   is raised.
7. An agentic customer-service assistant is governed on the AI-system track: evaluation
   results, red-team findings and agent action logs are assessed.
8. Reports and an examination package (zip with SHA-256 manifest) are produced.

All data are synthetic (see ``aimrg.datasets``).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from aimrg.checks import run_control_checks
from aimrg.core.classify import classify
from aimrg.core.models import (
    AICategory,
    AssetRecord,
    EvidenceItem,
    EvidenceType,
    FindingStatus,
)
from aimrg.datasets import (
    FEATURES,
    SCORE,
    TARGET,
    synthetic_credit_portfolio,
    synthetic_genai_evidence,
    synthetic_lending_decisions,
)
from aimrg.export import export_package
from aimrg.registry import Registry
from aimrg.reports import markdown_to_docx, render
from aimrg.tiering import assess_asset
from aimrg.workflow import transition

DEVELOPER = "developer@example-bank"
VALIDATOR = "validator@example-bank"
FAIR_LENDING = "fair-lending@example-bank"
APPROVER = "model-risk-committee@example-bank"
HERE = Path(__file__).resolve().parent


def _record(
    registry: Registry,
    asset_id: str,
    control_id: str,
    kind: EvidenceType,
    summary: str,
    actor: str,
    content: bytes | None = None,
) -> EvidenceItem:
    uri = digest = None
    if content is not None:
        uri, digest = registry.store_artifact(content, suffix=".md")
    return registry.add_evidence(
        EvidenceItem(
            asset_id=asset_id,
            control_id=control_id,
            type=kind,
            summary=summary,
            payload_uri=uri,
            payload_sha256=digest,
            pack_version=registry.pack.manifest.ref,
            actor=actor,
        )
    )


def main(out_dir: Path) -> Registry:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / "aimrg.db"
    if db.exists():
        sys.exit(f"{db} already exists; choose an empty output directory")

    # Data: scored samples, and application decisions with attributions and proxy groups.
    data = synthetic_credit_portfolio(seed=7)
    for role, frame in data.items():
        frame.to_csv(out_dir / f"{role}.csv", index=False)
    decisions = synthetic_lending_decisions(seed=7)
    decisions.to_csv(out_dir / "decisions.csv", index=False)
    reason_map = out_dir / "reasons.yaml"
    shutil.copyfile(HERE / "reasons.yaml", reason_map)

    registry = Registry.create(db, pack="fin-us-banking")
    common = {"target": TARGET, "score": SCORE, "features": FEATURES}

    # 1. Inventory, classification, tiering.
    bureau = registry.register(
        AssetRecord(
            name="Bureau Attribute Pipeline",
            owner="data-engineering",
            business_uses=["feature_engineering"],
            ai_category=AICategory.TRADITIONAL,
            description="Derives credit-bureau attributes used by several models.",
        )
    )
    model = registry.register(
        AssetRecord(
            name="Card Underwriting PD",
            owner="credit-risk-modeling",
            business_uses=["credit_decision"],
            ai_category=AICategory.ML_NON_GENERATIVE,
            description="Logistic probability-of-default model for card applications.",
            depends_on=[bureau.id],
        )
    )
    registry.set_classification(
        bureau.id,
        classify(
            ai_category=AICategory.TRADITIONAL,
            is_model_sr26_2=False,
            rationale="Deterministic transformations of bureau data; no statistical estimation.",
            jurisdictions=["US"],
        ),
    )
    registry.set_classification(
        model.id,
        classify(
            ai_category=AICategory.ML_NON_GENERATIVE,
            is_model_sr26_2=True,
            consequential_decision=True,
            rationale="Estimates default probability used to approve or decline applicants.",
            jurisdictions=["US", "US-CO"],
        ),
    )
    assess_asset(
        registry,
        bureau.id,
        {"complexity": 1, "assumptions": 1, "data_quality": 2, "exposure": 3, "purpose": 2},
        assessed_by=VALIDATOR,
    )
    assess_asset(
        registry,
        model.id,
        {"complexity": 3, "assumptions": 3, "data_quality": 2, "exposure": 5, "purpose": 5},
        assessed_by=VALIDATOR,
    )
    transition(registry, model.id, "development", actor=DEVELOPER)

    # 2. Development testing and documentation.
    _record(
        registry,
        model.id,
        "FIN-DEV-001",
        EvidenceType.DOCUMENT,
        "Model purpose and intended-use statement.",
        DEVELOPER,
        b"# Purpose\n",
    )
    run_control_checks(
        registry,
        model.id,
        "FIN-DEV-002",
        {"dev": data["dev"], "population": data["oot"]},
        actor=DEVELOPER,
        **common,
    )
    _record(
        registry,
        model.id,
        "FIN-DEV-002",
        EvidenceType.DOCUMENT,
        "Development data review.",
        DEVELOPER,
        b"# Data review\n",
    )
    run_control_checks(
        registry,
        model.id,
        "FIN-DEV-003",
        {"dev": data["dev"], "test": data["test"], "oot": data["oot"]},
        actor=DEVELOPER,
        **common,
    )
    _record(
        registry,
        model.id,
        "FIN-DEV-003",
        EvidenceType.DOCUMENT,
        "Development testing report.",
        DEVELOPER,
        b"# Testing\n",
    )
    transition(registry, model.id, "validation", actor=DEVELOPER)

    # 3. Independent validation, fair lending and adverse-action review.
    run_control_checks(
        registry,
        model.id,
        "FIN-VAL-003",
        {"dev": data["dev"], "oot": data["oot"]},
        actor=VALIDATOR,
        **common,
    )
    memo = b"# Validation memo (example)\n\nNarrative written by the validator.\n"
    for control_id, kind, summary in [
        ("FIN-VAL-003", EvidenceType.ATTESTATION, "Out-of-time results independently replicated."),
        ("FIN-VAL-001", EvidenceType.DOCUMENT, "Validation memo v1."),
        ("FIN-VAL-001", EvidenceType.ATTESTATION, "Validator independent of development."),
        ("FIN-VAL-002", EvidenceType.DOCUMENT, "Conceptual soundness review (memo section 3)."),
        ("FIN-VAL-002", EvidenceType.ATTESTATION, "Conceptual soundness reviewed."),
    ]:
        _record(
            registry,
            model.id,
            control_id,
            kind,
            summary,
            VALIDATOR,
            memo if kind is EvidenceType.DOCUMENT else None,
        )

    run_control_checks(
        registry,
        model.id,
        "FIN-CPR-001",
        {"decisions": decisions},
        actor=VALIDATOR,
        params={"reason_map": str(reason_map)},
    )
    _record(
        registry,
        model.id,
        "FIN-CPR-001",
        EvidenceType.DOCUMENT,
        "Adverse-action reason methodology.",
        VALIDATOR,
        b"# Reason methodology\n",
    )
    fairness_runs = run_control_checks(
        registry,
        model.id,
        "FIN-CPR-002",
        {"decisions": decisions},
        actor=FAIR_LENDING,
        score=SCORE,
        params={"group_prob_prefix": "p_", "control_group": "group_a"},
    )
    review = _record(
        registry,
        model.id,
        "FIN-CPR-002",
        EvidenceType.DOCUMENT,
        "Fair-lending review: drivers of the score gap, business justification and "
        "less-discriminatory-alternative search.",
        FAIR_LENDING,
        b"# Fair-lending review (example)\n",
    )
    # The failed screening metric opened a finding; the review is its remediation evidence.
    for run in fairness_runs:
        if run.finding is not None:
            registry.save_finding(
                run.finding.model_copy(
                    update={"status": FindingStatus.CLOSED, "remediation_evidence": [review.id]}
                )
            )
    transition(
        registry,
        model.id,
        "approved",
        actor=VALIDATOR,
        approver=APPROVER,
        note="Approved by model risk committee.",
    )

    # 4. Implementation, then production.
    _record(
        registry,
        model.id,
        "FIN-CPR-004",
        EvidenceType.DOCUMENT,
        "Adverse-action notice template with FCRA disclosures.",
        DEVELOPER,
        b"# Notice\n",
    )
    _record(
        registry,
        model.id,
        "FIN-CPR-005",
        EvidenceType.DOCUMENT,
        "Colorado ADMT consumer notice.",
        DEVELOPER,
        b"# CO notice\n",
    )
    for control_id, summary in [
        ("FIN-CPR-005", "Colorado pre-use notice reviewed by compliance."),
        ("FIN-CPR-006", "Colorado 30-day adverse-outcome disclosure, aligned with Reg B notice."),
        ("FIN-CPR-007", "Data-correction and human-review procedure."),
    ]:
        if control_id != "FIN-CPR-005":
            _record(
                registry,
                model.id,
                control_id,
                EvidenceType.DOCUMENT,
                summary,
                DEVELOPER,
                f"# {control_id}\n".encode(),
            )
        _record(registry, model.id, control_id, EvidenceType.ATTESTATION, summary, VALIDATOR)
    transition(registry, model.id, "in_production", actor=DEVELOPER)

    # 5. Two monitoring cycles: the first is clean, the second finds drift and a change
    #    request follows.
    for period in ("oot", "current"):
        run_control_checks(
            registry,
            model.id,
            "FIN-MON-001",
            {"baseline": data["dev"], "current": data[period]},
            actor=VALIDATOR,
            **common,
        )
        run_control_checks(
            registry, model.id, "FIN-MON-002", {"current": data[period]}, actor=VALIDATOR, **common
        )
    transition(
        registry,
        model.id,
        "change_requested",
        actor=VALIDATOR,
        note="Recalibrate: discrimination and calibration deteriorated in monitoring.",
    )

    # 6. An agentic AI assistant on the AI-system track (outside SR 26-2's scope).
    genai = synthetic_genai_evidence(seed=7)
    for role, frame in genai.items():
        frame.to_csv(out_dir / f"{role}.csv", index=False)
    assistant = registry.register(
        AssetRecord(
            name="Customer Service Assistant",
            owner="customer-operations",
            business_uses=["customer_service"],
            ai_category=AICategory.AGENTIC,
            vendor="Example LLM Provider",
            description="LLM-based agent that answers questions and can open tickets or "
            "issue small refunds.",
        )
    )
    registry.set_classification(
        assistant.id,
        classify(
            ai_category=AICategory.AGENTIC,
            is_model_sr26_2=False,
            third_party=True,
            rationale="Agentic LLM application; governed on the AI-system track.",
            jurisdictions=["US"],
        ),
    )
    assess_asset(
        registry,
        assistant.id,
        {"complexity": 5, "assumptions": 3, "data_quality": 3, "exposure": 3, "purpose": 2},
        assessed_by=VALIDATOR,
    )
    run_control_checks(
        registry,
        assistant.id,
        "FIN-AIS-003",
        {"evaluations": genai["evaluations"], "red_team": genai["red_team"]},
        actor=VALIDATOR,
    )
    run_control_checks(
        registry,
        assistant.id,
        "FIN-AIS-004",
        {"agent_actions": genai["agent_actions"]},
        actor=VALIDATOR,
        params={
            "allowed_tools": "kb_search,account_lookup,create_ticket,issue_refund",
            "human_approval_tools": "issue_refund",
        },
    )
    (out_dir / "ai_system_record.md").write_text(
        render(registry, "ai_system_record", assistant.id), encoding="utf-8"
    )

    # 7. Documents, an examination package and an integrity check.
    for template in (
        "validation_report",
        "fair_lending_report",
        "adverse_action_reasons",
        "colorado_admt_notices",
    ):
        text = render(registry, template, model.id)
        (out_dir / f"{template}.md").write_text(text, encoding="utf-8")
    markdown_to_docx(
        (out_dir / "validation_report.md").read_text(encoding="utf-8"),
        out_dir / "validation_report.docx",
        title="Validation report",
    )
    (out_dir / "inventory_report.md").write_text(
        render(registry, "inventory_report"), encoding="utf-8"
    )

    package = export_package(registry, out_dir / "examination-package.zip")
    audit = registry.verify()
    open_findings = [f for f in registry.findings(model.id) if f.status is FindingStatus.OPEN]
    print(f"Registry:          {db}")
    print(f"Status:            {registry.get(model.id).status.value}")
    print(f"Evidence recorded: {audit.evidence_checked} item(s); chains intact: {audit.ok}")
    print(f"Open findings:     {len(open_findings)}")
    print(f"Reports:           {out_dir}/*.md, validation_report.docx")
    print(f"Package:           {package.path} (SHA256SUMS {package.sums_sha256[:16]}...)")
    return registry


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("credit-scorecard-demo")
    main(target).close()
