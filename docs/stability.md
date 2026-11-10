# Versioning and stability

Institutions build processes and evidence around their governance tools, so AI-MRG says
plainly what may change and how changes are announced.

## Two version numbers

| | Versioned as | Example |
|---|---|---|
| **The software** (`ai-mrg` on PyPI) | Semantic Versioning | `0.4.0` |
| **Each pack** (`pack.yaml` → `version`) | Semantic Versioning, independently | `fin-us-banking@0.1.0` |

The registry records the pack reference (`id@version`) it was created with, and every
evidence item records the control it satisfies. This means you can always tell which
version of the guidance a piece of evidence was produced under.

## What counts as the public interface

These are covered by the compatibility promise below:

1. **The CLI**: command names, options, exit codes (0 on success; `aimrg check run`
   exits 2 when a check fails; `aimrg audit` and `aimrg export verify` exit 1 on
   integrity problems; other errors exit non-zero), and the files it writes (CSV,
   JSON, Markdown, `.docx`, examination packages).
2. **The REST API**: paths, methods, request fields, response fields and status codes,
   as described by `/openapi.json`. Adding response fields is not a breaking change.
3. **The pack format**: the JSON Schemas in `schemas/pack-*.schema.json` and the
   expression language for `applies_when`.
4. **Data formats**: `schemas/asset-record`, `evidence-item`, `finding` and
   `risk-assessment`, the evidence hash chain, and the examination package layout
   (`manifest.json`, `SHA256SUMS`).
5. **The registry**: existing registries keep opening in newer versions. Schema changes
   are applied automatically by migrations, and **evidence and lifecycle history are
   never rewritten** by a migration.

Not covered: Python modules and functions (they may change in any minor release before
1.0; `aimrg.__version__` is the only exported name), the dashboard's layout, report
wording, and anything named with a leading underscore.

## Before 1.0 (now)

- **Minor releases** (`0.x.0`) may change the public interface. Every such change is listed
  under **Changed** or **Removed** in the CHANGELOG, with a migration note.
- Where practical, a deprecated CLI option or API field keeps working for **one minor
  release** and emits a warning (`DeprecationWarning` in Python, a stderr warning in the
  CLI, a `Deprecation` header in the API).
- **Patch releases** (`0.x.y`) contain fixes only.
- Registries created by any 0.x release open in later 0.x releases.

## From 1.0

- Breaking changes to the public interface only in **major** releases.
- Deprecations announced at least **one minor release** (and at least 3 months) before
  removal.
- Security fixes back-ported to the latest minor release of the current major version.

## Pack versioning

| Change | Pack version bump |
|---|---|
| Control removed, or its objective made stricter or looser; `applies_when` changed so a control applies to different assets; threshold made stricter | **Major** |
| New control; new optional evidence; threshold relaxed with explanation; new template | **Minor** |
| Citation, wording or typo fixes that don't change meaning; new source links | **Patch** |

A pack's `reviewed` flag and `reviews:` list apply to the version that was reviewed.
A major or minor change in meaning sends the affected controls back for review before
the next version is marked reviewed (see [Reviewing a pack](review/guide.md)).

Before 1.0 a pack may use pre-release versions (`0.1.0-dev`), which make no
compatibility promise.

## Regulatory change

When a source changes (for example, a supervisory letter is replaced, as SR 11-7 was by
SR 26-2), the pack gets a new version. The previous version stays available so evidence
can be read against the guidance in force when it was produced.
