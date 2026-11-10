# Threat model

This page describes what AI-MRG protects, who it protects it from, and how. It covers
version 0.4 and is reviewed at each minor release. To report a vulnerability, see
[SECURITY.md](https://github.com/abiroland/ai-mrg-framework/blob/main/SECURITY.md).

## What we protect

| Asset | Why it matters |
|---|---|
| **Evidence and lifecycle history** | The record an examiner or auditor relies on. Its value depends on it not being silently changed |
| **Approval decisions** | Must be made by an independent person with the right role |
| **Artifacts** (uploaded documents, check results) | Referenced by evidence; tampering would undermine it |
| **API tokens and webhook secrets** | Grant access and authenticate outbound notifications |
| **Scored datasets** passed to checks | May contain customer-level data |
| **Pack content** | Drives what controls apply and how checks run |

## Trust boundaries and deployment assumptions

- AI-MRG runs **inside the institution's network**, operated by its staff. There is no
  hosted service and no telemetry.
- Whoever administers the host or the database (DBA, cloud admin) is **trusted**: they can
  in principle alter anything. AI-MRG makes such changes **detectable**, not impossible.
- API clients (people, the dashboard, pipelines) are **authenticated but not fully
  trusted**: a developer may want to approve their own model, and a viewer should not
  change anything.
- Packs from the official repository are reviewed through pull requests. **Third-party
  packs are untrusted input.**
- TLS termination is the deployer's responsibility (reverse proxy or load balancer).

## Threats and mitigations

| # | Threat | Mitigation | Residual risk |
|---|---|---|---|
| T1 | **Evidence tampering**: editing or deleting an evidence item or lifecycle event to hide a failure | Append-only tables enforced by database triggers on both SQLite and PostgreSQL; each item hashes its content and the previous item's hash; `aimrg audit` re-verifies every chain and artifact digest; examination packages carry `SHA256SUMS` and can be verified independently | A database superuser can drop the triggers and rewrite a whole chain. Mitigate by keeping off-host copies of exported packages or periodic chain heads (hashes) |
| T2 | **Artifact substitution**: replacing an uploaded file | Content-addressed storage (path = SHA-256); the digest is recorded in the evidence item; `aimrg audit` re-hashes artifacts | Same as T1 for someone with file-system access |
| T3 | **Path traversal** through artifact URIs or upload names | `artifact_path` rejects absolute paths and `..`; upload suffixes are reduced to a plain extension or dropped | None known |
| T4 | **Privilege escalation / self-approval** | Every API request authenticated; the authenticated user is always recorded as the actor (clients cannot set it); role checks on each route; workflow independence rules (developers cannot approve their own model; tier-1 approvals need an approver who did not validate it) | Role assignment is CLI-only and therefore depends on host access controls |
| T5 | **Token theft or guessing** | Tokens are 256-bit random values, shown once and stored only as SHA-256 hashes; tokens can be disabled; no token in URLs | Tokens sent over plain HTTP could be intercepted. Always deploy behind TLS |
| T6 | **Code execution through packs** | Pack `applies_when` rules use a restricted expression language parsed with `ast` and an allow-list of node types: no calls, attribute access, imports or comprehensions; packs are YAML loaded with `safe_load`; schemas are validated with pydantic; check IDs map only to built-in Python functions | A malicious pack can still give **misleading guidance** (wrong citations, weak thresholds). Use reviewed packs and check `reviewed:` / `reviews:` in `pack.yaml` |
| T7 | **Template injection** in reports | Jinja2 sandboxed environment; templates come from the pack (see T6) | As T6 |
| T8 | **Denial of service through large uploads** | Request bodies capped (`AIMRG_MAX_UPLOAD_MB`, default 50 MB), checked from `Content-Length` and while streaming | Large-but-allowed datasets can still use CPU; run checks on appropriately sized hosts |
| T9 | **Spreadsheet formula injection** in files people open in Excel | The pack review workbook and the reason-code CSVs written by `aimrg reasons derive` neutralize cells starting with `=`, `+`, `-`, `@`, tab or CR | None known |
| T10 | **Unintended data egress** | No outbound calls except webhooks an admin configures; webhook payloads contain identifiers and statuses, not datasets; signed with HMAC-SHA256 (`X-AIMRG-Signature`) | Admins should point webhooks only at internal endpoints |
| T11 | **Sensitive data at rest** | Checks store summary metrics, not row-level data; uploaded documents are stored as given | Uploaded documents and the database are not encrypted by AI-MRG. Use encrypted volumes and database encryption |
| T12 | **Supply-chain compromise** | Dependencies scanned with `pip-audit` on every CI run; CodeQL static analysis; Dependabot updates; PyPI trusted publishing (no stored tokens); releases carry SLSA build-provenance attestations, PyPI attestations, a CycloneDX SBOM and `SHA256SUMS` | Upstream compromise between scans. Pin versions in regulated deployments |
| T13 | **Browser-side attacks** on API responses | `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on every response; JSON API with no cookies (no CSRF surface) | The Streamlit dashboard inherits Streamlit's security model; put it behind your SSO proxy |

## Verifying a release

```bash
pip download ai-mrg==0.4.0 --no-deps -d dl
gh attestation verify dl/ai-mrg-0.4.0-py3-none-any.whl --repo abiroland/AI-MRG-Framework
```

Compare against `SHA256SUMS` on the GitHub release, and review the CycloneDX SBOM
attached there for the full dependency list.

## Hardening checklist for deployers

- [ ] Serve the API and dashboard only over TLS, behind your SSO or VPN
- [ ] Use PostgreSQL with a dedicated, non-superuser application role
- [ ] Restrict who can run the `aimrg user` and `aimrg webhook` commands on the host
- [ ] Encrypt the database and artifact volumes; back them up
- [ ] Schedule `aimrg audit` and alert on failure
- [ ] Keep exported examination packages (with checksums) outside the main system
- [ ] Use reviewed packs and pin the AI-MRG version
- [ ] Set `AIMRG_MAX_UPLOAD_MB` to the largest dataset you expect
