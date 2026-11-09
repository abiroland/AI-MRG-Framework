# ADR-0002: Regulatory controls are versioned data, not code

**Status:** Accepted · **Date:** 2026-11-09

## Context
In 2026 alone, SR 11-7 was replaced by SR 26-2, and Colorado's AI law was repealed and replaced before it took effect. If controls were hard-coded, every regulatory change would require a software release and the code would mix with legal content.

## Decision
Controls, applicability rules, thresholds, tiering profiles, citations and document templates live in **YAML/Jinja2 content packs**. Packs carry a semantic version and an effective-as-of date, and are validated against a JSON Schema. Python code implements only generic mechanics (resolution, checks, evidence, rendering).

## Consequences
- Compliance professionals can review and propose changes without writing Python.
- Institutions can fork or override a pack locally (for example, to tighten thresholds) without patching code.
- Every evidence record and document stores the pack version it was produced under.
- A small, safe expression language is needed for `applies_when`. No arbitrary code execution.
