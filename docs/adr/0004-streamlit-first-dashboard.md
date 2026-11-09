# ADR-0004: Streamlit for the first dashboard, behind a stable API

**Status:** Accepted · **Date:** 2026-11-09

## Context
The project wants both a Python library and a web dashboard. With one part-time maintainer, a full React front end would delay the first usable UI by months.

## Decision
Build the v0.3 dashboard in **Streamlit**. It talks to the FastAPI service, not directly to the database. All business logic stays in the core and the API.

## Consequences
- A usable UI ships quickly, in the same language as the rest of the project.
- Streamlit has limits on fine-grained access control and complex workflows. Mitigation: the API enforces roles and separation of duties, so the UI is only a client.
- Because the API contract is stable, a later React (or other) front end can replace Streamlit without changing the core.
