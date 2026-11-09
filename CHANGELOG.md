# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

Packs are versioned separately; their changes are listed under **Packs**.

## [Unreleased]

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
