"""End-to-end AI-MRG walkthrough on a simulated credit-card underwriting model.

Run from anywhere:

    python examples/credit-scorecard/run_example.py [OUTPUT_DIR]

It creates OUTPUT_DIR (default: ./credit-scorecard-demo) containing:

* the scored datasets as CSV (dev, test, oot, current),
* ``aimrg.db``, the governance registry with all recorded evidence,
* ``validation_report.md`` / ``.docx`` and ``inventory_report.md``.

The data are synthetic (see ``aimrg.datasets``). The model passes development and
validation testing, then deteriorates in the monitored period, so monitoring checks fail
and open findings automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aimrg.checks import run_control_checks
from aimrg.core.classify import classify
from aimrg.core.models import AICategory, AssetRecord, EvidenceItem, EvidenceType
from aimrg.datasets import FEATURES, SCORE, TARGET, synthetic_credit_portfolio
from aimrg.registry import Registry
from aimrg.reports import markdown_to_docx, render
from aimrg.tiering import assess_asset

ACTOR_DEV = "developer@example-bank"
ACTOR_VAL = "validator@example-bank"


def main(out_dir: Path) -> Registry:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / "aimrg.db"
    if db.exists():
        sys.exit(f"{db} already exists; choose an empty output directory")

    # 1. Data: scored samples for development, hold-out, out-of-time and monitoring.
    data = synthetic_credit_portfolio(seed=7)
    for role, frame in data.items():
        frame.to_csv(out_dir / f"{role}.csv", index=False)

    # 2. Registry bound to the U.S. banking pack.
    registry = Registry.create(db, pack="fin-us-banking")

    # 3. Inventory: an upstream feature pipeline and the PD model that uses it.
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

    # 4. Classification and risk tiering.
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
        assessed_by=ACTOR_VAL,
    )
    assess_asset(
        registry,
        model.id,
        {"complexity": 3, "assumptions": 3, "data_quality": 2, "exposure": 5, "purpose": 5},
        assessed_by=ACTOR_VAL,
    )

    # 5. Development testing, recorded by the developer.
    common = {"target": TARGET, "score": SCORE, "features": FEATURES}
    run_control_checks(
        registry,
        model.id,
        "FIN-DEV-002",
        {"dev": data["dev"], "population": data["oot"]},
        actor=ACTOR_DEV,
        **common,
    )
    run_control_checks(
        registry,
        model.id,
        "FIN-DEV-003",
        {"dev": data["dev"], "test": data["test"], "oot": data["oot"]},
        actor=ACTOR_DEV,
        **common,
    )

    # 6. Independent validation, recorded by the validator.
    run_control_checks(
        registry,
        model.id,
        "FIN-VAL-003",
        {"dev": data["dev"], "oot": data["oot"]},
        actor=ACTOR_VAL,
        **common,
    )
    memo_uri, memo_sha = registry.store_artifact(
        b"# Validation memo (example)\n\nNarrative written by the validator.\n", suffix=".md"
    )
    for control_id, kind, summary in [
        ("FIN-VAL-003", EvidenceType.ATTESTATION, "Out-of-time results independently replicated."),
        ("FIN-VAL-001", EvidenceType.DOCUMENT, "Validation memo v1."),
        ("FIN-VAL-001", EvidenceType.ATTESTATION, "Validator independent of development."),
        ("FIN-VAL-002", EvidenceType.DOCUMENT, "Conceptual soundness review (in memo §3)."),
        ("FIN-VAL-002", EvidenceType.ATTESTATION, "Conceptual soundness reviewed."),
    ]:
        registry.add_evidence(
            EvidenceItem(
                asset_id=model.id,
                control_id=control_id,
                type=kind,
                summary=summary,
                payload_uri=memo_uri if kind is EvidenceType.DOCUMENT else None,
                payload_sha256=memo_sha if kind is EvidenceType.DOCUMENT else None,
                pack_version=registry.pack.manifest.ref,
                actor=ACTOR_VAL,
            )
        )

    # 7. A monitoring cycle: the population and performance have drifted.
    run_control_checks(
        registry,
        model.id,
        "FIN-MON-001",
        {"baseline": data["dev"], "current": data["current"]},
        actor=ACTOR_VAL,
        **common,
    )
    run_control_checks(
        registry,
        model.id,
        "FIN-MON-002",
        {"current": data["current"]},
        actor=ACTOR_VAL,
        **common,
    )

    # 8. Documents and an integrity check of the audit trail.
    report = render(registry, "validation_report", model.id)
    (out_dir / "validation_report.md").write_text(report, encoding="utf-8")
    markdown_to_docx(report, out_dir / "validation_report.docx", title="Validation report")
    inventory = render(registry, "inventory_report")
    (out_dir / "inventory_report.md").write_text(inventory, encoding="utf-8")

    audit = registry.verify()
    print(f"Registry:          {db}")
    print(f"Evidence recorded: {audit.evidence_checked} item(s); chains intact: {audit.ok}")
    print(f"Open findings:     {len(registry.findings(model.id))}")
    print(f"Reports:           {out_dir / 'validation_report.md'}, .docx, inventory_report.md")
    return registry


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("credit-scorecard-demo")
    main(target).close()
