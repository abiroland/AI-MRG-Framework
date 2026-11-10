# ADR-0006: PostgreSQL backend and token-based API access

**Status:** Accepted · **Date:** 2026-11-09 · **Builds on:** ADR-0005

## Context
Phase 2 needs multi-user access: several validators and approvers working on one
inventory, and a REST API for integration with other systems. ADR-0005 deferred a second
storage backend to this point.

## Decisions

1. **A thin backend layer, not an ORM.** `aimrg.registry.backends` provides `SQLiteDatabase`
   and `PostgresDatabase` behind one small interface (`query`, `run`, `script`,
   `transaction`). The registry uses portable SQL with `?` placeholders. Records stay as
   validated JSON, so there is still no migration framework.
2. **Append-only in both backends.** PostgreSQL uses a `BEFORE UPDATE OR DELETE` trigger
   function on `evidence` and `events`, matching the SQLite triggers. Tests run the same
   suite against both backends.
3. **API tokens, not passwords.** Users get random tokens (`aimrg_` + 32 random bytes);
   only SHA-256 hashes are stored. Administration is CLI-only so that the API cannot be
   used to grant itself privileges.
4. **The caller is the actor.** The API never accepts an actor or approver from the
   request body; it records the authenticated user. Approval endpoints feed the
   authenticated user into the workflow's independence rules.
5. **Webhooks are opt-in and signed.** Consistent with ADR-0003, nothing leaves the
   machine unless an administrator configures a webhook. Payloads are signed with
   HMAC-SHA256; delivery failures never block governance actions.

## Consequences
- Institutions with SSO will want OIDC. Tokens are the minimum viable mechanism; OIDC
  can be added in front of the same role model without changing the API surface.
- Schema version 3 adds `users`, `webhooks` and an ordering column on `events`; older
  SQLite registries migrate automatically on open.
