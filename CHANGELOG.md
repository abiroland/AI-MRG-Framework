# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

Packs are versioned separately; their changes are listed under **Packs**.

## [Unreleased]

### Added (Phase 4a)
- **Pack review kit**: `aimrg pack review-sheet` exports a practitioner review workbook (`.xlsx` with dropdowns, or `.csv`; `ai-mrg[review]`); `aimrg pack review-summary` turns a returned workbook into publishable notes. `pack.yaml` gains a `reviews:` list, and a pack marked `reviewed: true` must list at least one review. Reviewer guide, published-reviews page and a "Pack review" issue form.
- **Pilot kit**: pilot guide (4–6 week plan, success measures, data handling), case-study template, and a factual use-statement template.
- **Security**: threat model with deployer hardening checklist; request-body limit for the API (`AIMRG_MAX_UPLOAD_MB`, default 50 MB; `aimrg serve --max-upload-mb`); `nosniff` and `no-store` response headers; `pip-audit` job in CI; CodeQL analysis; releases now carry SLSA build-provenance attestations, a CycloneDX SBOM and `SHA256SUMS`.
- **Versioning and stability policy** for the software, the pack format and packs.

### Changed
- Report templates render in Jinja's sandbox (packs are untrusted input).

### Fixed
- Uploaded file names could put path separators into stored artifact names; suffixes are now reduced to a plain extension or dropped.

## [0.3.0] — Phase 3

### Added (Phase 3b)
- **Dashboard** (`ai-mrg[dashboard]`, `aimrg dashboard`): inventory, asset controls/evidence/findings/lifecycle, findings aging, monitoring trends, report center and examination packages. Works only through the REST API, so roles and approval rules apply. Deep links via `?page=…&asset=…`.
- **MLflow integration** (`ai-mrg[mlflow]`, `aimrg mlflow import|sync|record`): import registered models into the inventory, write governance tags and the main report back to MLflow, and record runs as evidence.
- **Docker Compose**: `Dockerfile`, `docker-compose.yml` (PostgreSQL, API, dashboard) with idempotent bootstrap; smoke-tested in CI.
- **Release workflow** for PyPI trusted publishing (`release.yml`, `RELEASING.md`).
- API: `GET /assets/{ref}/check-results`, `GET /templates`.
- `aimrg init --if-missing`; assets record `external_refs`.
- The example runs two monitoring cycles (one clean, one with drift).

### Added (Phase 3a)
- **AI-system track checks**: `genai.evaluation_suite` (pass rates per category, required categories, category-specific thresholds, NIST AI 600-1 mapping), `genai.red_team_log`, `genai.agent_permission_audit`.
- `sensitivity.alternative_assumptions`; every check referenced by `fin-us-banking` is now implemented.
- **AI-system governance record** template with the twelve NIST AI 600-1 risks.
- **Colorado SB 26-189**: controls FIN-CPR-005 to FIN-CPR-008 (pre-use notice, 30-day adverse-outcome disclosure, data correction and human review, record retention) and FIN-TPM-004 (developer documentation); `colorado_admt_notices` draft template.
- **Examination packages**: `aimrg export create|verify`, `GET /export`; records, evidence, artifacts, reports, pack snapshot and `SHA256SUMS`.
- `aimrg.datasets.synthetic_genai_evidence`; the example now includes an agentic assistant and an examination package.

### Packs
- `fin-us-banking`: new Colorado and AI-system content above; thresholds `genai_pass_rate` and `genai_pass_rate.prompt_injection` (illustrative).

## [0.2.0] — Phase 2

### Added (Phase 2b)
- **PostgreSQL backend** (`pip install "ai-mrg[postgres]"`): `aimrg --db postgresql://... init --artifacts-dir ...`. Append-only triggers on evidence and lifecycle events in both backends; the full test suite runs against both (ADR-0006).
- **REST API** (`pip install "ai-mrg[api]"`, `aimrg serve`): assets, classification, tiering, plans, evidence uploads, check runs with CSV uploads, findings, lifecycle transitions, reports (Markdown/Word) and audit verification. Role-based access (viewer, developer, validator, approver, auditor, admin); the authenticated user is always the recorded actor and, for approvals, the approver.
- **Users and tokens**: `aimrg user add|list|disable|rotate-token`; tokens stored as SHA-256 hashes.
- **Webhooks**: `aimrg webhook add|list|test|remove`; HMAC-SHA256-signed notifications for `finding.opened`, `finding.resolved`, `check.failed` and `lifecycle.transition`; opt-in and non-blocking.
- `Registry.subscribe()` for in-process notifications.

### Changed
- Registry schema version 3 (`users`, `webhooks`, ordered lifecycle events); older SQLite registries migrate on open.
- `--db` accepts a PostgreSQL URL; `Registry.path` replaced by `Registry.location`.
- Artifact URIs that try to leave the artifacts directory are rejected.

### Added (Phase 2a)
- **Fair-lending checks**: `fairness.adverse_impact_ratio` (with two-proportion p-values) and `fairness.standardized_mean_difference`, using group labels or proxy membership probabilities.
- **Adverse-action reasons** (`aimrg.adverse_action`): reason maps, reason derivation from per-applicant attributions, and checks `adverse_action.reason_mapping` and `adverse_action.reason_specificity`; `aimrg reasons derive`.
- **Lifecycle workflow** (`aimrg.workflow`): gated transitions, independent-approver rule, `approved_with_limits`, approval evidence written automatically; `aimrg transition` and `aimrg status`. Lifecycle events are append-only.
- **Findings aging**: default due dates by severity, `aimrg finding aging`, overdue section in the inventory report.
- Report templates: `fair_lending_report`, `adverse_action_reasons`, `vendor_assessment`; lifecycle history in the validation report.
- `aimrg check run --param key=value`.
- `aimrg.datasets.synthetic_lending_decisions`; the example now runs the full lifecycle.

### Changed
- Registry schema version 2 (adds the `events` table); version-1 registries are migrated automatically on open.
- `Registry.save()` no longer changes lifecycle status; use the workflow. `Registry.set_status()` was removed.

## [0.1.0] — Phase 1

### Added
- **Registry** (`aimrg.registry`): SQLite-backed store for assets, evidence and findings; content-addressed artifact store; append-only evidence enforced by triggers and a per-asset hash chain; `verify()` for integrity audits (ADR-0005).
- **Bulk import** of inventories from CSV or YAML, all-or-nothing, with dependency ordering and cycle detection.
- **Classification wizard** and **risk tiering** with approved overrides and aggregate-risk flags from the dependency graph.
- **Control resolver**: required controls per asset and stage, with evidence coverage (`missing`, `partial`, `failing`, `complete`).
- **Checks** (`aimrg.checks`): `data.quality_summary`, `data.representativeness`, `performance.out_of_sample`, `performance.out_of_time`, `performance.discrimination_drift`, `stability.population_stability`, `outcomes.backtest`; plugin entry point `aimrg.checks`; runner that records results as evidence and opens findings on failure.
- **Reports**: validation report and inventory report templates (Markdown), with Word export (`ai-mrg[docx]`).
- **CLI**: `init`, `register`, `import`, `list`, `show`, `classify`, `tier`, `plan`, `check list|run`, `evidence add|list`, `finding list|open|close|accept`, `report list|render`, `audit verify`.
- `aimrg.datasets.synthetic_credit_portfolio` and an end-to-end example in `examples/credit-scorecard/`.

### Changed
- Control `checks[].thresholds` now hold full threshold definitions (`warn`, `fail`, `direction`).
- Evidence items record `check_id` and `outcome` for automated checks.

### Packs
- `fin-us-banking` 0.1.0-dev: added `backtest_relative_error` threshold and report templates; FIN-VAL-003 threshold override uses the new format.

## [0.0.1] — Phase 0

### Added
- Phase 0 foundations: package skeleton, domain models (asset, classification, risk assessment, evidence, finding), JSON Schemas, a safe expression language for control applicability, pack loader and validator, and the `aimrg` CLI (`pack list`, `pack validate`, `schema export`).
- CI (lint, type-check, test on Python 3.11–3.13) and a documentation site.
- Architecture, regulatory map, roadmap and ADRs 0001–0004.

### Packs
- `fin-us-banking` 0.1.0-dev: draft seed controls, sources register, default tiering profile and thresholds. **Not yet reviewed by practitioners.**
