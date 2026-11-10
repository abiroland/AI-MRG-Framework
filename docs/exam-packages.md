# Examination packages

An examination package is one zip file with everything an examiner, auditor or reviewer
needs for a set of assets, and a way to prove it has not been changed.

```bash
aimrg export create -o exam-2026Q4.zip                      # all assets
aimrg export create "Card Underwriting PD" -o pd-model.zip   # selected assets
aimrg export verify exam-2026Q4.zip
```

Or over the API (auditor role): `GET /export?asset=Card%20Underwriting%20PD`.

## Contents

| Path | What |
|---|---|
| `manifest.json` | format, AI-MRG version, pack version, assets, audit result at export time, missing artifacts, reports that could not be rendered |
| `records/assets/*.json` | asset records (classification, risk assessment, status) |
| `records/evidence/*.jsonl` | evidence items, in hash-chain order |
| `records/findings/*.json` | findings |
| `records/events/*.json` | lifecycle history |
| `artifacts/` | every stored artifact the evidence refers to (check results, documents) |
| `reports/<asset>/*.md` | the reports relevant to each asset, rendered from evidence |
| `reports/inventory_report.md` | portfolio inventory |
| `pack/` | snapshot of the governance pack used (controls, sources, thresholds, templates) |
| `SHA256SUMS` | SHA-256 of every other file |

Which reports are included depends on the asset: a validation report (or an AI-system
record for generative/agentic AI), fair-lending and adverse-action reports for credit
decisions, a vendor assessment for third-party assets, and draft Colorado notices where
Colorado's law applies.

## Integrity

`aimrg export verify` recomputes every hash and reports altered, missing or extra files.
The evidence inside also carries its own hash chain, so `records/evidence/*.jsonl` can
be re-verified independently.

On export, AI-MRG prints the SHA-256 of `SHA256SUMS`. Record that value somewhere
independent of the file (a ticket, an email to the examiner, a log), or sign
`SHA256SUMS` with your organization's tools. Then verify with:

```bash
aimrg export verify exam-2026Q4.zip --expect <sha256>
```
