# AI-MRG Framework

**An open-source, full-lifecycle AI and model risk governance toolkit for U.S. regulated industries — starting with financial services.**

> Status: **v0.1 (Phase 1)**. Inventory, classification, risk tiering, control planning, automated checks (data, performance, stability, outcomes), a tamper-evident evidence trail, findings, and validation/inventory reports in Markdown and Word all work from the `aimrg` CLI and Python. Fair-lending, adverse-action and GenAI checks come next. See [`docs/ROADMAP.md`](docs/ROADMAP.md). Pack content has **not yet been reviewed by practitioners**.

[![CI](https://github.com/abiroland/ai-mrg-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/abiroland/ai-mrg-framework/actions/workflows/ci.yml)

---

## Why this exists

U.S. guidance tells institutions *what* good AI and model governance looks like. Turning that into day-to-day practice is the hard part. That means inventories, risk tiers, validation evidence, fairness testing, adverse-action reasons, monitoring thresholds and audit-ready documents. Enterprise platforms do this well, but they are licensed products. Community banks, credit unions, fintech start-ups and academic researchers often cannot justify the cost.

The 2026 regulatory reset makes the gap sharper:

- **SR 26-2 / OCC Bulletin 2026-13 (April 17, 2026)** replaced SR 11-7. It is principles-based and risk-based, sets no enforceable standards and is aimed mainly at banks over $30B in assets. **It explicitly excludes generative and agentic AI.** Institutions are told to cover those with their own governance practices.
- **ECOA / Regulation B** still require specific, accurate adverse-action reasons, whatever the model's complexity (CFPB Circulars 2022-03 and 2023-03).
- **Colorado SB 26-189** brings automated-decision-making obligations for lending and other consequential decisions starting **January 1, 2027**.
- **NIST AI RMF 1.0** and its **Generative AI Profile (NIST AI 600-1)** remain the main voluntary reference. A revision is in progress.

AI-MRG turns these sources into **versioned control catalogs, automated checks, evidence trails and document templates** that any institution can run locally, with no license fee, no vendor account and no telemetry.

## What it will do

| Capability | Description |
|---|---|
| **Inventory & classification** | Register every model and AI system. Classify it as *model* under SR 26-2, or as GenAI or agentic AI, and route it to the right governance track. |
| **Risk tiering** | Configurable scoring on inherent risk, exposure, purpose, materiality and aggregate risk, using SR 26-2's risk factors. |
| **Control catalog** | Lifecycle controls stored as data (YAML), each with its regulatory references, required evidence and applicability rules. |
| **Checks library** | Python checks for data quality, performance, stability, outcomes analysis, fair lending and adverse-action reason quality. |
| **Evidence & audit trail** | Append-only, hash-chained evidence records linked to controls, findings and approvals. |
| **Document generation** | Development documents, validation reports, monitoring reports, adverse-action reason maps and inventory reports. |
| **Dashboard** | A web UI for inventory, review workflows, findings and monitoring. |

## What it is not

- Not legal advice, and not a certification. Using AI-MRG does not by itself make an institution compliant.
- Not a replacement for independent validation or human judgment. It structures and evidences that work.
- Not a hosted service. You run it yourself.

## Quick start

```bash
git clone https://github.com/abiroland/ai-mrg-framework
cd ai-mrg-framework
pip install -e ".[dev]"

# A complete run on a simulated credit model: registry, evidence, findings, reports
python examples/credit-scorecard/run_example.py demo
```

Then follow [Getting started](docs/getting-started.md) for the step-by-step workflow (`aimrg init → register → classify → tier → plan → check run → report render → audit verify`). See [Checks](docs/checks.md) and [Writing packs](docs/packs.md) for details.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): system design, modules, data model, interfaces
- [Regulatory map](docs/REGULATORY_MAP.md): how controls trace to SR 26-2, ECOA/Reg B, NIST AI RMF, Colorado and others
- [Roadmap](docs/ROADMAP.md): phased delivery plan and success metrics
- [Architecture decision records](docs/adr/)

## Scope by release

- **Financial services (U.S. banking and consumer lending)**: first, v0.1–v1.0
- **Healthcare**: planned as a second sector pack on the same core, after v1.0

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Regulatory content changes must cite a primary source.

## License

Apache License 2.0. See [ADR-0001](docs/adr/0001-license-apache-2.md).
