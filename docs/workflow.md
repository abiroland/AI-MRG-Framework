# Lifecycle workflow

Every asset has a lifecycle status. Status changes only through `aimrg transition`
(or `aimrg.workflow.transition` in Python), which checks a **gate** and records an
append-only lifecycle event. `Registry.save()` refuses status changes, so the gates cannot
be bypassed by accident.

```bash
aimrg status "Card Underwriting PD"          # history, and what blocks each next step
aimrg transition "Card Underwriting PD" validation
aimrg transition "Card Underwriting PD" approved --approver "Model Risk Committee"
```

## Allowed moves

```
intake ─► development ─► validation ─┬─► approved ──────────────┐
                ▲             ▲      ├─► approved_with_limits ──┼─► in_production ─► change_requested
                │             │      └─► rejected ─► development │                         │
                │             └──────────────────────────────────┼─────────────────────────┘
                └─ (from validation, to rework)                  └─► retired (from most states)
```

`approved_with_limits` can later move to `approved` once validation is complete.

## Gates

| Moving to | Requires |
|---|---|
| `development` | the asset is classified and risk-tiered |
| `validation` | every development-stage control is complete |
| `approved` | every validation-stage control is complete; no open **high** or **critical** findings; an independent approver |
| `approved_with_limits` | documented limits (`--limits`); no open **critical** findings; an independent approver |
| `rejected` | an independent approver and a note |
| `in_production` | every implementation-stage control is complete (e.g. adverse-action notices, state disclosures) |
| `change_requested` | a note describing the change |
| `retired` | every retirement-stage control is complete |

"Complete" means every evidence type the control requires is recorded and its latest
automated checks did not fail. If a check failed, the control still counts as satisfied
once every finding raised against that control has been **closed** (with remediation
evidence) or **risk-accepted** by a named person.

### Independent approval

The approver may not be the asset owner, and may not be anyone who recorded
development-stage evidence for the asset. This reflects SR 26-2's attention to
conflicts of interest between development and validation. Approval decisions are also
written to the evidence trail as `approval` evidence against the pack's approval-stage
controls (`FIN-VAL-004` in `fin-us-banking`).

### Urgent use: approved with limits

SR 26-2 recognizes that a model may sometimes be needed before validation is finished.
`approved_with_limits` allows that, but only with the limits written down. Record
heightened monitoring and stakeholder notification as evidence, and move to `approved`
once validation completes.

## Findings aging

Findings opened by failing checks, or with `aimrg finding open` without `--due`, get a
default due date by severity:

| Severity | Days to remediate |
|---|---|
| critical | 30 |
| high | 60 |
| medium | 90 |
| low | 180 |

These are starting points; set your own policy with `--due`.

```bash
aimrg finding aging              # all open findings, most overdue first
aimrg finding aging "Card Underwriting PD"
```

The inventory report lists overdue findings for the model risk committee.
