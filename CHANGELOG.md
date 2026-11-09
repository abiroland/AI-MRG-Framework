# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

Packs are versioned separately; their changes are listed under **Packs**.

## [Unreleased]

### Added
- Phase 0 foundations: package skeleton, domain models (asset, classification, risk assessment, evidence, finding), JSON Schemas, a safe expression language for control applicability, pack loader and validator, and the `aimrg` CLI (`pack list`, `pack validate`, `schema export`).
- CI (lint, type-check, test on Python 3.11–3.13) and a documentation site.
- Architecture, regulatory map, roadmap and ADRs 0001–0004.

### Packs
- `fin-us-banking` 0.1.0-dev: draft seed controls, sources register, default tiering profile and thresholds. **Not yet reviewed by practitioners.**
