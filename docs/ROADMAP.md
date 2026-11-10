# AI-MRG Framework — Roadmap

*Assumes one core maintainer working part-time (~10–15 hrs/week). Durations are estimates; each phase ends with a tagged release.*

---

## Phase 0 — Foundations (weeks 1–3) → `v0.0.x`

**Goal:** a public repo that looks like a serious open-source project from day one.

- [x] Repo hygiene: `LICENSE` (Apache-2.0), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `GOVERNANCE.md`, issue and PR templates
- [x] Package skeleton (`src/aimrg`), `pyproject.toml` with extras, ruff, mypy, pytest
- [x] GitHub Actions CI: lint, type-check, test on Python 3.11–3.13
- [x] MkDocs site published to GitHub Pages (architecture, regulatory map, roadmap)
- [x] Domain models (Pydantic) and their JSON Schemas: AssetRecord, Classification, RiskAssessment, Control, EvidenceItem, Finding
- [x] Control-catalog YAML schema and validator (`aimrg pack validate`)

**Exit criteria:** CI green; `pip install -e .` works; schema docs published.

*Also delivered early:* a draft `fin-us-banking` pack (32 controls, 10 sources, default tiering profile, thresholds) and the safe applicability-expression language from Phase 1.

---

## Phase 1 — MVP: inventory, tiering, core validation (weeks 4–10) → `v0.1.0`

**Goal:** a model validator can take a credit model from registration to a rendered validation report.

- [x] Registry with SQLite storage; bulk import from CSV/YAML
- [x] Classification wizard (SR 26-2 model test; GenAI/agentic routing; consequential-decision flag)
- [x] Tiering engine with the `fin-us-banking/default` profile
- [x] Control resolver and safe expression language
- [x] `fin-us-banking` pack v0.1: inventory, development, validation and monitoring controls (~30 controls)
- [x] Checks: `data`, `performance`, `stability`, `outcomes`
- [x] Evidence store with hash chain; `aimrg audit verify`
- [x] Templates: validation report, inventory/tiering report (Markdown + DOCX)
- [x] CLI: `init`, `register`, `tier`, `plan`, `check run`, `report render`
- [x] **Worked example:** an end-to-end credit scorecard (`examples/credit-scorecard/`). *Uses simulated data; a version on a public dataset is still to do.*
- [ ] First PyPI release *(needs a PyPI account and trusted-publishing setup by the maintainer)*

**Exit criteria:** the example notebook runs end to end from a clean install; test coverage ≥ 80% on core.

*Also delivered:* findings tracking from the CLI (`finding open/close/accept`), automatic findings for failing checks, evidence recording (`evidence add/list`), and a plugin entry point for third-party checks. Findings aging reports remain in Phase 2.

---

## Phase 2 — Consumer protection & monitoring (weeks 11–18) → `v0.2.0`

**Goal:** cover what most sets U.S. lending apart: fair lending and adverse action.

- [x] Fair-lending checks: adverse impact ratio, standardized mean difference, proxy-probability support; fair-lending summary template *(in core; no extra needed)*
- [ ] Fair-lending worked example on public HMDA data *(a simulated example ships now)*
- [x] Adverse-action reasons: derivation from attributions, mapping and specificity checks, methodology template, `aimrg reasons derive`
- [x] Ongoing monitoring: scheduled check runs (exit codes for schedulers), thresholds, breach → finding, signed webhook alerts
- [x] Findings tracking with aging reports
- [x] Lifecycle workflow engine with gated transitions, separation of duties and the `approved_with_limits` path
- [x] Third-party/vendor model controls and assessment template
- [x] REST API (FastAPI) with role-based access and separation-of-duties checks
- [x] Postgres support

**Exit criteria:** a full lifecycle (intake → approval → 3 monitoring cycles → finding → remediation) is demonstrable through both the SDK and the API.

---

## Phase 3 — Dashboard, AI-system track, state law (weeks 19–28) → `v0.3.0`

**Goal:** non-coders can use it, and it covers what SR 26-2 leaves out.

- [ ] Streamlit dashboard: inventory, asset detail, findings board, monitoring trends, report center
- [ ] **AI-system track** for GenAI and agentic AI, built on NIST AI RMF and AI 600-1: use-case intake, risk profile, human-oversight design, evaluation evidence, incident log
- [ ] `genai` extra: adapters to open-source evaluation harnesses, red-team test registry, agent action/permission audit records
- [ ] Colorado SB 26-189 content: covered-ADMT scoping questions, disclosure and notice templates (ready before the **Jan 1, 2027** effective date, then updated as rules settle)
- [ ] MLflow integration
- [ ] Examination package export (signed zip)
- [ ] Docker Compose deployment (API + dashboard + Postgres)

**Exit criteria:** a non-technical model owner can register, review and approve through the UI alone.

---

## Phase 4 — Hardening & adoption (weeks 29–40) → `v1.0.0`

- [ ] External review of the `fin-us-banking` pack by practitioners (validators, compliance officers); publish the review notes
- [ ] Pilots with 1–3 institutions or consultancies (community banks, credit unions, fintechs), with written case studies (anonymized if needed)
- [ ] Security review; signed releases; SBOM
- [ ] Stable API and pack schema (semantic versioning commitment)
- [ ] Contributor onboarding: "good first issue" backlog, a contributor guide for writing new checks and packs
- [ ] Healthcare pack design document (HIPAA, FDA AI-enabled device guidance, clinical fairness) — the start of the second sector

---

## Success metrics

Track these from day one. They show whether the project is working, and they serve as objective evidence of its reach.

| Metric | Source | v0.1 target | v1.0 target |
|---|---|---|---|
| PyPI downloads (monthly) | pypistats | baseline | sustained growth |
| GitHub stars / forks | GitHub | baseline | — |
| External contributors (merged PRs) | GitHub | 0–1 | 5+ |
| Institutions piloting or using it | case studies, letters | — | 1–3 documented |
| Practitioner reviews of packs | published notes | — | ≥ 3 reviewers |
| Talks, workshops, publications | log | 1 | 3+ |
| Citations or references by others | search | — | track |

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Regulations change mid-build (as SR 26-2 and Colorado did in 2026) | Controls-as-data; pack versioning with effective-as-of dates |
| Overclaiming compliance | Clear disclaimers; templates never auto-assert compliance; human attestations required |
| Solo-maintainer bus factor | Early contributor recruitment; good docs; small, modular codebase |
| Scope creep (healthcare too early) | Healthcare is gated behind v1.0 |
| Novelty challenged by similar tools | Keep the positioning table in ARCHITECTURE.md current and factual |
