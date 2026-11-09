# ADR-0005: Standard-library SQLite for the v0.1 registry

**Status:** Accepted · **Date:** 2026-11-09 · **Amends:** the storage line in ARCHITECTURE §9

## Context
The architecture proposed SQLAlchemy/SQLModel with SQLite by default and Postgres for multi-user installs. For v0.1 the registry has one user at a time, a handful of tables, and records that are validated Pydantic models.

## Decision
Use Python's built-in `sqlite3` module for v0.1. Store each record as validated JSON, with a few indexed columns (ids, names, sequence numbers). Enforce evidence immutability with database triggers that reject `UPDATE` and `DELETE`, in addition to the hash chain. Keep all database access inside `aimrg.registry.store.Registry`.

## Consequences
- No ORM dependency; a registry is one portable file plus an artifacts folder.
- The Pydantic models stay the single source of truth for structure, so schema changes need no migrations for most fields.
- Postgres support (Phase 2) will add a second implementation behind the same `Registry` interface. That is the point at which an ORM or query layer may be introduced, recorded in a new ADR.
- Triggers are a guard, not a guarantee: someone with file access can drop them. Tamper *evidence* comes from the hash chain and `aimrg audit verify`.
