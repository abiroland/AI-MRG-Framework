# Regulatory Map — `fin-us-banking` pack

*Effective-as-of: November 2026 · Informational only, not legal advice. Check every citation against the primary source before relying on it.*

This map shows which external sources each AI-MRG control area draws on. Control-level citations live in each control's `references` in `packs/fin-us-banking/controls/`.

---

## 1. Source register

| ID | Source | Status (Nov 2026) | Binding? | What AI-MRG takes from it |
|---|---|---|---|---|
| `SR-26-2` | Fed SR 26-2 / OCC Bulletin 2026-13 / FDIC (interagency), *Revised Guidance on Model Risk Management*, Apr 17, 2026 | In force; supersedes SR 11-7, SR 21-8 and OCC 2011-12 | Guidance. No enforceable standards, but unsafe or unsound practices can still draw action | Model definition, risk-based tiering factors, development testing, validation, ongoing monitoring, outcomes analysis, third-party models, inventory, documentation, internal audit. **Generative and agentic AI are out of scope.** |
| `ECOA-REGB` | Equal Credit Opportunity Act, 15 U.S.C. § 1691; Regulation B, 12 CFR 1002 (incl. § 1002.9 notices) | In force | Law | Adverse-action notices with specific, principal reasons; anti-discrimination |
| `CFPB-2022-03` | CFPB Circular 2022-03: adverse action and complex algorithms | Published | Interpretive | Complexity or opacity is no excuse for missing or inaccurate reasons |
| `CFPB-2023-03` | CFPB Circular 2023-03: adverse action and proper use of Reg B sample forms | Published | Interpretive | Reasons must be specific and reflect the factors actually scored, not the nearest sample-form reason |
| `FCRA` | Fair Credit Reporting Act, adverse-action provisions | In force | Law | Extra notice content when a consumer report is used |
| `FHA` | Fair Housing Act | In force | Law | Fair-lending testing for mortgage and housing-related credit |
| `TPRM-2023` | Interagency Guidance on Third-Party Relationships: Risk Management (June 2023) | In force | Guidance | Vendor due diligence, contracts, ongoing monitoring |
| `NIST-AI-RMF-1.0` | NIST AI 100-1 (Jan 2023) | Current; revision in progress | Voluntary | GOVERN / MAP / MEASURE / MANAGE outcomes; the backbone of the AI-system track |
| `NIST-AI-600-1` | NIST Generative AI Profile (Jul 2024) | Current | Voluntary | GenAI-specific risks and actions |
| `CO-SB26-189` | Colorado SB 26-189 (automated decision-making technology), repealing and replacing SB 24-205 | Signed May 14, 2026; **effective Jan 1, 2027**; related litigation ongoing | Law (state) | Disclosure and consumer-rights duties for covered ADMT in lending and other consequential decisions |

> **Monitor:** the NIST AI RMF revision, any SR 26-2 FAQs or follow-ups, CFPB policy changes, Colorado rulemaking and litigation, and other state ADMT laws. Each update ships as a new pack version.

---

## 2. Control areas × sources

✔ = primary source for the control area · ○ = supporting reference

| Control area | SR-26-2 | ECOA / Reg B + CFPB circulars | FCRA | FHA | TPRM-2023 | NIST AI RMF / 600-1 | CO SB 26-189 |
|---|---|---|---|---|---|---|---|
| **Inventory** | ✔ | | | | ○ | ✔ (MAP) | ○ |
| **Classification / scoping** | ✔ (model definition; GenAI/agentic exclusion) | ○ | | | | ✔ | ✔ (covered ADMT) |
| **Risk tiering** | ✔ (inherent risk, exposure, purpose, materiality, aggregate) | | | | | ✔ (MAP 5) | |
| **Development standards** | ✔ (purpose statement, user input, testing) | | | | | ✔ (MEASURE) | |
| **Independent validation** | ✔ (conceptual soundness, rigor ∝ materiality, before first use) | | | | | ✔ (MEASURE) | |
| **Use with limits (urgent use)** | ✔ | | | | | ○ | |
| **Ongoing monitoring** | ✔ | ○ | | | ○ | ✔ (MANAGE) | |
| **Outcomes analysis / back-testing** | ✔ | | | | | ✔ | |
| **Fair-lending testing** | ○ | ✔ | | ✔ | | ✔ (fairness) | ✔ |
| **Adverse-action reasons** | | ✔ | ✔ | | | ○ (explainability) | ✔ |
| **Third-party / vendor models** | ✔ | ○ | | | ✔ | ✔ (GOVERN 6) | ○ |
| **Change management / new uses** | ✔ | | | | | ✔ | |
| **Documentation** | ✔ | ○ | | | ○ | ✔ | ✔ |
| **Governance, roles, internal audit** | ✔ | | | | ○ | ✔ (GOVERN) | |
| **GenAI / agentic AI controls** | — (out of scope) | ○ (when used in credit) | | | ○ | ✔ | ○ |
| **Consumer notices & disclosures** | | ✔ | ✔ | | | ○ | ✔ (pre-use notice, 30-day adverse-outcome disclosure) |
| **Consumer rights after adverse outcomes** | | ✔ | | | | ○ (MANAGE 4.1) | ✔ (data correction, human review) |
| **Record retention** | ✔ | ✔ | | | | ○ | ✔ |

---

## 3. Notes on interpretation

1. **SR 26-2 is guidance, and it is risk-based.** AI-MRG's default thresholds and evidence lists are *starting points*. Institutions should tune them and record why. Smaller institutions are not automatically held to large-bank expectations.
2. **The GenAI/agentic gap.** Because SR 26-2 excludes these systems, AI-MRG handles them on a separate *AI-system track* built mainly on NIST AI RMF and NIST AI 600-1. When such a system touches a credit decision, ECOA/Reg B obligations still apply in full.
3. **Fair-lending methods are contested.** Proxy methods (such as BISG) and less-discriminatory-alternative searches involve legal and statistical judgment. AI-MRG provides tools and records how they were used. It does not decide what is legally sufficient.
4. **State law is moving fast.** Colorado's law was rewritten before its first effective date. Treat state-law content in packs as fast-changing.
