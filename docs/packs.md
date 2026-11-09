# Writing packs

A **pack** is versioned regulatory content for one sector: sources, controls, a tiering profile and thresholds. It is plain YAML, so compliance professionals can review and propose changes without writing Python ([ADR-0002](adr/0002-controls-as-data.md)).

## Layout

```
packs/<name>/
├── pack.yaml            # manifest: name, version, effective_as_of, sector, reviewed
├── sources.yaml         # every regulation, guidance or standard the pack cites
├── controls/*.yaml      # controls, grouped by area
├── tiering/*.yaml       # one tiering profile per file
└── thresholds/*.yaml    # warn/fail levels for automated checks
```

Run `aimrg pack validate packs/<name>` after every change. It reports **all** problems at once, with file names.

## A control

```yaml
- id: FIN-VAL-003                      # PREFIX-AREA-NNN, unique in the pack
  title: Independent replication of out-of-time performance
  objective: >
    Confirm the model performs as expected on data from a period not used in development.
  lifecycle_stage: validation          # governance | inventory | development | validation |
                                       # approval | implementation | monitoring | change | retirement
  applies_when: track == "model" and tier <= 2
  frequency: at_validation             # once | at_validation | on_change | continuous |
                                       # monthly | quarterly | annually
  evidence_required: [check_result, attestation]
  checks:
    - id: performance.out_of_time
      thresholds:                      # optional overrides of pack defaults
        gini_drop: { warn: 0.05, fail: 0.10, direction: above }
  references:
    - { source: SR-26-2, section: Model validation }
  guidance: Optional practical notes for implementers.
```

Rules the validator enforces:

- Every `references[].source` must exist in `sources.yaml`.
- A control with `checks` must require `check_result` evidence.
- `applies_when` must be a valid expression (below).

## Applicability expressions

`applies_when` uses a small, safe language. It is parsed and evaluated by a whitelist walker, never by `eval`, so pack content cannot execute code.

| Variable | Meaning |
|---|---|
| `track` | `"model"`, `"ai_system"` or `"non_model"` |
| `tier` | effective risk tier, 1 (highest) to 4 (immaterial) |
| `ai_category` | `"traditional"`, `"ml_non_generative"`, `"generative"`, `"agentic"` |
| `uses` | list of business-use tags, e.g. `["credit_decision"]` |
| `consequential_decision` | influences a consequential consumer decision |
| `third_party` | vendor or third-party provided |
| `jurisdictions` | list of codes, e.g. `["US", "US-CO"]` |
| `stage` | current lifecycle status |

Allowed: `and`, `or`, `not`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, string/number literals, `true`/`false`, and lists of literals. Not allowed: function calls, attribute access, indexing, arithmetic.

```yaml
applies_when: '"credit_decision" in uses and tier <= 3'
applies_when: consequential_decision and "US-CO" in jurisdictions
applies_when: ai_category in ["generative", "agentic"]
```

!!! tip
    Quote an expression that starts with a string literal (`'"credit_decision" in uses'`) so YAML does not treat it as a quoted scalar.

## Editor support

Each pack file starts with a `yaml-language-server` comment pointing to the published JSON Schema, which gives autocompletion and inline errors in VS Code and other editors.
