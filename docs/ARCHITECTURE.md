# AI-MRG Framework — Architecture

*Version 0.1 (draft) · Sector focus: U.S. financial services*

---

## 1. Goals and constraints

### Goals
1. **Operationalize principles.** Turn principles-based guidance (SR 26-2, NIST AI RMF) and hard legal requirements (ECOA/Reg B adverse action, fair lending) into concrete, testable controls with evidence.
2. **Cover the whole lifecycle.** Intake → development → validation → approval → deployment → monitoring → change → retirement.
3. **Cover both governance tracks.** Traditional and ML *models* (under SR 26-2) and *AI systems* that SR 26-2 leaves out (generative and agentic AI).
4. **Scale to the institution.** Proportionate, risk-based rigor. A $500M credit union and a $50B bank should both be able to use it without drowning in process.
5. **Be audit-ready by default.** Every decision, test result and approval leaves a tamper-evident trail and can be rendered into examiner-friendly documents.

### Constraints
- **Local-first, no account, no telemetry.** Institutions must be able to run it inside their own network. See [ADR-0003](adr/0003-local-first.md).
- **Regulatory content is data, not code.** Rules change faster than software. Controls, mappings and thresholds live in versioned YAML. See [ADR-0002](adr/0002-controls-as-data.md).
- **Small core, optional extras.** `pip install ai-mrg` should be light. Heavy dependencies (SHAP, fairness libraries, LLM evaluation) are extras.
- **Sector-agnostic core, sector-specific packs.** Financial services ships first as a *pack*. Healthcare reuses the same core later.

### Non-goals
- Legal interpretation or certification.
- Model training or MLOps orchestration. AI-MRG integrates with MLflow and similar tools; it does not replace them.
- A hosted SaaS.

---

## 2. System overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Interfaces                                 │
│   Python SDK  ·  CLI (aimrg)  ·  REST API (FastAPI)  ·  Dashboard     │
└───────────────┬──────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────┐
│                       Governance engine (core)                       │
│                                                                      │
│  Inventory &      Risk tiering     Lifecycle /       Findings &       │
│  classification → engine        →  workflow engine → issue tracking   │
│        │               │                 │                │          │
│        └───────────────┴──────┬──────────┴────────────────┘          │
│                               ▼                                      │
│              Control resolver (which controls apply?)                │
│                               │                                      │
│        ┌──────────────────────┼──────────────────────┐               │
│        ▼                      ▼                      ▼               │
│   Checks runner        Evidence store &        Document generator    │
│   (automated tests)    audit trail             (templates → MD/DOCX/PDF)
└────────┬───────────────────────┬─────────────────────┬───────────────┘
         │                       │                     │
┌────────▼─────────┐  ┌──────────▼─────────┐  ┌────────▼──────────────┐
│  Checks library  │  │  Storage           │  │  Content packs        │
│  core · fairness │  │  SQLite / Postgres │  │  fin-us-banking (v1)  │
│  explain · genai │  │  + artifact store  │  │  healthcare-us (later)│
└──────────────────┘  └────────────────────┘  └───────────────────────┘
```

---

## 3. Core concepts (domain model)

| Entity | Purpose | Key fields |
|---|---|---|
| **AssetRecord** | Anything under governance: a model or an AI system | `id`, `name`, `owner`, `business_use`, `asset_type`, `track`, `status`, `vendor`, `upstream/downstream deps` |
| **Classification** | Decides the governance track | `is_model_sr26_2` (bool + rationale), `ai_category` ∈ {traditional, ml_non_generative, generative, agentic}, `consequential_decision` (bool), `jurisdictions` |
| **RiskAssessment** | Tier and the reasons for it | factor scores (inherent, exposure, purpose), `materiality`, `aggregate_flags`, `tier` ∈ {1,2,3,4}, `override` + justification, `assessed_by`, `date` |
| **Control** | One governance requirement (from a pack) | `id`, `title`, `objective`, `lifecycle_stage`, `applies_when` (expression), `evidence_required[]`, `checks[]`, `references[]`, `frequency` |
| **Check** | An automated test that produces evidence | `id`, `implementation` (Python entry point), `params`, `thresholds`, `output_schema` |
| **EvidenceItem** | Proof that a control was done | `control_id`, `asset_id`, `type` (check_result / document / attestation / approval), `payload_uri`, `sha256`, `prev_hash`, `actor`, `timestamp` |
| **Finding** | A problem raised during validation, monitoring or audit | `severity`, `control_id`, `description`, `owner`, `due_date`, `status`, `remediation_evidence[]` |
| **LifecycleEvent** | A state change | `from_stage`, `to_stage`, `approver`, `conditions` (e.g. "use with limits") |
| **Pack** | Versioned sector content | `controls/`, `checks config`, `templates/`, `mappings/`, `tiering profile`, `version` |

All entities are Pydantic v2 models, and their JSON Schemas are published so other tools can interoperate.

---

## 4. Modules

### 4.1 Inventory & classification
- Registers assets through the SDK, CLI, API or a bulk CSV/YAML import. Bulk import matters for day-one adoption, because most institutions already keep a spreadsheet inventory.
- **Classification wizard.** A short decision tree:
  1. Is it a complex quantitative method that turns inputs into quantitative estimates using statistical, economic or financial theory? → **SR 26-2 model**. (Simple arithmetic and deterministic rule-based software are excluded.)
  2. Is it generative AI or agentic AI? → **AI-system track**, because SR 26-2 explicitly excludes these.
  3. Does it make, or substantially influence, a consequential decision about a consumer (credit, housing, insurance, employment)? → adds **consumer-protection controls** (adverse action, fair lending, ADMT disclosures).
  4. Is it vendor or third-party? → adds **third-party controls**.
- An asset can be both. For example, a GenAI tool that drafts adverse-action letters goes on the AI-system track *and* gets consumer-protection controls.

### 4.2 Risk tiering engine
- A **tiering profile** (YAML, inside a pack) defines factors, scoring scales, weights and tier cut-offs. The default fin-us-banking profile uses SR 26-2's factors:
  - **Inherent risk**: complexity, number and criticality of assumptions, input data quality and constraints
  - **Exposure**: how significant the output is to decisions (portfolio size, volume, dollar impact)
  - **Purpose**: regulatory or financial-risk use scores higher
  - **Materiality**: exposure × purpose
  - **Aggregate risk**: shared data, assumptions or methods across assets (computed from the dependency graph)
  - **Institution size and complexity**: a profile-level setting that scales required rigor
- Output: a tier from 1 (highest) to 4 (immaterial), with a full explanation. Immaterial assets need only identification and periodic re-check for materiality.
- Overrides are allowed but require a justification and an approver, and both are recorded as evidence.

### 4.3 Lifecycle / workflow engine
- A state machine per asset: `intake → development → validation → approved | approved_with_limits | rejected → in_production → (monitoring) → change_requested → retired`.
- Transitions are **gated by controls**. For example, `validation → approved` requires every Tier-applicable validation control to have evidence and no open findings above a severity threshold.
- **Urgent-use path.** SR 26-2 allows use before full validation when the need is urgent. The engine supports `approved_with_limits`, which requires documented limits, stakeholder notification and tighter monitoring frequency.
- **Use-change detection.** Registering a new business use on an existing asset re-opens the relevant validation controls.

### 4.4 Control resolver
- Takes an asset's classification, tier and lifecycle stage, evaluates each pack control's `applies_when` expression, and returns the required control set.
- Expressions use a small, safe expression language (no `eval`), for example:
  `track == "model" and tier <= 2 and "credit_decision" in uses`.

### 4.5 Checks library

| Package | Checks (initial set) | Extra |
|---|---|---|
| `aimrg.checks.data` | schema conformance, missingness, range and outlier tests, train/test leakage, population representativeness | core |
| `aimrg.checks.performance` | discrimination (AUC/Gini/KS), calibration (Hosmer–Lemeshow, calibration curves), out-of-sample and out-of-time comparison, benchmark/challenger comparison | core |
| `aimrg.checks.stability` | PSI, CSI, characteristic drift, score distribution shift | core |
| `aimrg.checks.outcomes` | back-testing vs realized outcomes, outlier analysis, threshold breach detection | core |
| `aimrg.checks.sensitivity` | assumption sensitivity, alternative-assumption comparison | core |
| `aimrg.checks.fairness` | adverse impact ratio, standardized mean difference, marginal effects; proxy-based protected-class estimation via a pluggable method (e.g. BISG); less-discriminatory-alternative search hooks | `[fairness]` |
| `aimrg.checks.adverse_action` | reason-code generation from attributions; reason **specificity** and **accuracy** checks (are reasons tied to the factors actually scored? are generic sample-form reasons being misapplied?); reason stability across similar applicants | `[explain]` |
| `aimrg.checks.genai` | evaluation-harness adapters (groundedness, toxicity, PII leakage, prompt-injection test suites), human-review sampling, agent action logging and permission audits | `[genai]` (Phase 3) |

Each check:
- is a plain Python callable registered through an entry point (`aimrg.checks`), so third parties can add checks without forking;
- returns a typed `CheckResult` (metrics, pass/warn/fail against thresholds, plots, a narrative stub);
- is deterministic given the same inputs and seed, so it can be re-run as part of an audit.

### 4.6 Evidence store & audit trail
- **Metadata** goes in SQLite (default) or Postgres (multi-user).
- **Artifacts** (datasets, plots, reports) are stored by content address (SHA-256) on the local filesystem or an S3-compatible bucket.
- **Tamper evidence.** Each evidence record stores `prev_hash`, forming a hash chain per asset. `aimrg audit verify` re-computes the chain.
- **Who and when.** Every write records the actor and a timestamp. Approvals are explicit attestation records.
- **Export.** A complete "examination package" (inventory, tiering, evidence, findings, documents) as a signed zip.

### 4.7 Document generator
- Jinja2 templates in packs render to Markdown, then to **DOCX** (python-docx) and **PDF**.
- Initial templates (fin-us-banking):
  - Model development document
  - Independent validation report
  - Ongoing monitoring report
  - Vendor/third-party model assessment
  - Adverse-action reason-code mapping and justification
  - Fair lending testing summary
  - Model inventory and tiering report (board/committee level)
  - AI-system governance record (GenAI and agentic track)
  - ADMT consumer disclosure and notice templates (Colorado SB 26-189)
- Templates pull directly from evidence. Narrative sections are left as clearly marked placeholders for human authors. **No auto-generated narrative claims compliance.**

### 4.8 Findings & issue tracking
- Findings are created by humans, or automatically when a check fails a hard threshold.
- Each finding has a severity, owner, due date and status. Closing one requires remediation evidence.
- Aging reports show overdue findings by tier and owner.

---

## 5. Interfaces

### 5.1 Python SDK (primary)
```python
import aimrg

reg = aimrg.Registry.open("governance.db")
asset = reg.register(
    name="Retail Card Underwriting v3",
    owner="credit-risk@bank.example",
    business_use=["credit_decision"],
    ai_category="ml_non_generative",
)
asset.assess_risk(profile="fin-us-banking/default")      # → Tier 1
plan = asset.required_controls(stage="validation")

results = aimrg.run_checks(
    asset, plan,
    model=model, train=train_df, test=test_df, oot=oot_df,
    protected_proxy="bisg",
)
asset.render("validation_report", fmt="docx")
```

### 5.2 CLI
`aimrg init`, `aimrg register`, `aimrg tier`, `aimrg plan`, `aimrg check run`, `aimrg evidence add`, `aimrg finding open|close`, `aimrg report render`, `aimrg audit verify`, `aimrg export`.

### 5.3 REST API (FastAPI)
- Same operations as the SDK, with OpenAPI docs.
- Auth: local accounts for small installs, OIDC for institutions.
- Role-based access: developer, validator, model owner, approver, auditor (read-only), admin. Developers cannot approve their own models; this enforces the separation SR 26-2 asks for between development and validation.

### 5.4 Dashboard
- **v0.3: Streamlit** on top of the API. It is fast to build and fits a solo-maintainer phase. See [ADR-0004](adr/0004-streamlit-first-dashboard.md).
- Views: inventory (filter by tier, track, status), asset detail (lifecycle, controls, evidence), findings board, monitoring trends, report center.
- A later React front end is possible if adoption demands it. The API contract stays stable either way.

---

## 6. Integrations
- **ML frameworks:** scikit-learn, XGBoost, LightGBM, statsmodels (adapters expose `predict`, `predict_proba` and feature metadata).
- **MLflow:** import registered models and runs into the inventory, and attach evidence back to MLflow runs.
- **Data:** pandas and Polars DataFrames; Parquet/CSV.
- **Notifications:** webhook for monitoring breaches and overdue findings.
- **Plugin points:** `aimrg.checks`, `aimrg.packs`, `aimrg.renderers`, `aimrg.storage`.

---

## 7. Content packs

```
packs/fin-us-banking/
├── pack.yaml                 # name, version, effective-as-of date, maintainers
├── tiering/default.yaml      # factors, weights, cut-offs
├── controls/
│   ├── inventory.yaml
│   ├── development.yaml
│   ├── validation.yaml
│   ├── monitoring.yaml
│   ├── third_party.yaml
│   ├── consumer_protection.yaml   # adverse action, fair lending, ADMT
│   └── ai_systems.yaml            # GenAI/agentic track
├── mappings/                 # control → source citations (see REGULATORY_MAP.md)
├── thresholds/default.yaml   # PSI, AIR, AUC-drop alert levels (institution-tunable)
└── templates/                # Jinja2 document templates
```

Example control (illustrative):

```yaml
- id: FIN-VAL-004
  title: Out-of-time performance testing
  objective: >
    Confirm the model performs as expected on data from a period not used in development.
  lifecycle_stage: validation
  applies_when: track == "model" and tier <= 3
  frequency: at_validation
  checks:
    - id: performance.oot_comparison
      thresholds: { gini_drop_warn: 0.05, gini_drop_fail: 0.10 }
  evidence_required: [check_result, validator_attestation]
  references:
    - source: SR-26-2
      section: "Model development — testing"
    - source: NIST-AI-RMF-1.0
      section: "MEASURE 2"
```

Packs carry an **effective-as-of date**, and every rendered document records which pack version produced it. That way an examiner can see which version of the rules a decision was made under.

---

## 8. Proposed repository layout

```
ai-mrg/
├── src/aimrg/
│   ├── core/          # domain models, registry, resolver, workflow
│   ├── tiering/
│   ├── checks/        # data, performance, stability, outcomes, fairness, adverse_action, genai
│   ├── evidence/      # store, hash chain, export
│   ├── reports/       # renderer
│   ├── api/           # FastAPI app
│   ├── cli/
│   └── dashboard/     # Streamlit app
├── packs/fin-us-banking/
├── examples/
│   └── credit-scorecard/   # end-to-end walkthrough on public data
├── docs/                   # MkDocs site source
├── tests/
└── .github/workflows/      # lint, type-check, test, release
```

---

## 9. Technology choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python ≥ 3.11 | It's what model developers and validators already use |
| Schemas | Pydantic v2 + JSON Schema | Validation, plus interoperability |
| Storage | SQLAlchemy 2 / SQLModel; SQLite default, Postgres optional | Zero-setup start; scales to multi-user |
| API | FastAPI | Typed, generates OpenAPI |
| Dashboard | Streamlit (v0.3) | Fastest path to a usable UI |
| Docs rendering | Jinja2 → Markdown → python-docx / PDF | Examiner-friendly formats |
| Explainability | SHAP (optional extra) | Industry standard for attributions |
| Fairness | in-house metrics + pluggable proxy method | Keeps the core light; avoids hard dependency choices |
| Quality | ruff, mypy, pytest, hypothesis | Tooling credible enough for regulated users |
| Docs site | MkDocs Material | Simple, searchable |
| Packaging | PyPI (`ai-mrg`), with extras `[fairness]`, `[explain]`, `[genai]`, `[api]`, `[dashboard]` | Light default install |

---

## 10. Security and privacy
- No telemetry and no outbound calls by default.
- Evidence can reference sensitive data without copying it (store a hash and a pointer instead of the payload).
- PII redaction utilities for report rendering.
- Signed releases and a published SBOM. Dependabot and a `SECURITY.md` disclosure process.
- Role separation enforced in the API (developer ≠ validator ≠ approver for the same asset).

---

## 11. Positioning against existing tools

| Tool | What it does | How AI-MRG differs |
|---|---|---|
| Commercial MRM / AI-governance platforms | Full workflows, hosted | AI-MRG is free, self-hosted and has no vendor account |
| ValidMind Library | Open-source validation tests and documentation automation, paired with the ValidMind Platform (account required) | AI-MRG covers inventory, tiering, workflow and audit trail end to end without a hosted platform |
| Microsoft Agent Governance Toolkit | Open-source runtime governance for AI agents, with HIPAA/SOC 2 mappings | AI-MRG is lifecycle *model-risk* governance with U.S. lending law built in (adverse action, fair lending); it could integrate agent-runtime tools on its AI-system track |
| Fairness / explainability libraries | Point solutions for one technique | AI-MRG orchestrates them as checks tied to controls and evidence |
| NIST AI RMF & Playbook | Sector-agnostic outcomes and suggested actions | AI-MRG is a sector-specific, executable implementation that maps back to the RMF |

> Keep this table factual and up to date. It is the basis for any "novelty" claim, and reviewers will check it.

---

## 12. Open questions
1. **Fairness proxies.** Ship a BISG implementation, or only adapters to existing open-source ones? (Licensing and census-data packaging both matter.)
2. **GenAI evaluation.** Build a thin adapter layer over existing eval harnesses, or define our own minimal suite?
3. **Multi-tenant.** Is one database per institution enough, or will consultants need multi-institution workspaces?
4. **Pack governance.** Who reviews pack changes? Consider an advisory group of practitioners before v1.0.
