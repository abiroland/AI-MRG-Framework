# Piloting AI-MRG

A pilot is a short, low-risk trial of AI-MRG at a bank, credit union, fintech or
consultancy. The goal is to learn whether it saves time and improves governance for a real
model or AI system, and what needs to change.

## Shape of a pilot

| | |
|---|---|
| **Length** | 4–6 weeks |
| **Scope** | 1–3 models or AI systems, chosen by you (one credit model is a good start) |
| **People** | A model owner or developer, a validator, and a sponsor in model risk or compliance; about 2 hours a week each |
| **Where it runs** | On your own infrastructure (a laptop, a VM, or Docker Compose). AI-MRG has no hosted service and no telemetry, and makes no outbound calls unless you configure a webhook |
| **Data** | Start with the bundled simulated data. Use your own scored datasets only when your data-governance process allows it; data never leaves your environment |
| **Cost** | None. AI-MRG is Apache-2.0 open source |

## Plan

| Week | What happens |
|---|---|
| 0 | Kick-off call: choose the models, agree success measures, get internal approvals (vendor or open-source software review, data use) |
| 1 | Install (`pip install ai-mrg` or Docker Compose). Run the example. Import or register your inventory |
| 2 | Classify and tier the pilot models. Review the required controls (`aimrg plan`). Map existing evidence |
| 3–4 | Run the automated checks on your scored data. Record documents and attestations. Try the lifecycle workflow and the reports |
| 5 | Try monitoring (a scheduled `check run`), findings, and the examination package |
| 6 | Wrap-up session: what worked, what didn't, what's missing. Decide whether to continue |

Maintainers are available for the kick-off, a weekly 30-minute check-in, and the wrap-up.

## Success measures (pick 2–4)

- Time to produce a validation or monitoring report, before vs. with AI-MRG
- Share of required controls with evidence after the pilot
- Number of issues found by automated checks that would otherwise have been missed or
  found later
- Validator and model-owner rating of the reports (1–5) and of the workflow
- Whether the examination package would satisfy your examiners or internal audit
  (your judgment)

## What we ask in return

- Honest feedback, including what doesn't work: issues on GitHub, or privately
- Permission to write up a **case study** if the pilot is useful, reviewed and approved by
  you before publication. You may be named or anonymous ("a U.S. community bank with
  about $2 billion in assets").

## Things to settle internally first

- Your institution's process for approving open-source software
- Whether pilot data may be used and where it will be stored
- That AI-MRG is a governance aid. It does not replace independent validation, legal
  review or your institution's policies, and using it does not by itself establish
  compliance.

## Templates

- [Case-study template](case-study-template.md)
- [Use statement template](use-statement-template.md): a short factual record of how
  your organization used AI-MRG, if you choose to provide one
