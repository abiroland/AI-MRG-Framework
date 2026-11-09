# ADR-0003: Local-first, no account, no telemetry

**Status:** Accepted · **Date:** 2026-11-09

## Context
Model governance evidence often contains confidential data (credit files, performance by segment, fair-lending results). Many institutions cannot send it to a third-party service. Several open-source tools in this space are tied to a vendor's hosted platform.

## Decision
AI-MRG runs entirely inside the user's environment. The default storage is a local SQLite file plus a local artifact directory; Postgres and S3-compatible storage are options. There is no account, no license key and no telemetry, and the software makes no outbound network calls unless a user configures an integration.

## Consequences
- Easy to approve for use inside regulated networks.
- No usage data comes back to the project, so adoption must be measured by other means (PyPI downloads, GitHub activity, voluntary case studies).
- Multi-user deployments need the API + Postgres setup (Phase 2).
