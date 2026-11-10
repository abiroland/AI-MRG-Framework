# REST API, users and webhooks

## Run the API

```bash
pip install -e ".[api]"            # or "ai-mrg[api]" once published
aimrg --db aimrg.db serve --host 127.0.0.1 --port 8000
```

Interactive documentation (OpenAPI) is served at `http://127.0.0.1:8000/docs`.

The server binds to `127.0.0.1` by default. To expose it to other machines, put it behind
your institution's reverse proxy with TLS; do not serve tokens over plain HTTP.

## Users, roles and tokens

API access is by bearer token. User administration is deliberately **CLI-only**:

```bash
aimrg user add dana --role developer
aimrg user add val  --role validator
aimrg user add mrc  --role approver
aimrg user add audit --role auditor
aimrg user list
aimrg user rotate-token dana       # old token stops working
aimrg user disable dana
```

The token is printed once; only a SHA-256 hash is stored.

| Role | Can |
|---|---|
| `viewer` | read assets, plans, evidence, findings, status and reports |
| `developer` | register assets, record documents and attestations, run checks, move assets toward validation |
| `validator` | everything a developer can, plus classify, tier, open and close findings |
| `approver` | approve, approve with limits, or reject; accept risk on findings |
| `auditor` | read everything and run `GET /audit/verify` |
| `admin` | everything |

The **authenticated user is always the actor** recorded on evidence, findings and
lifecycle events, so clients cannot act as someone else. For approval decisions the
caller is the approver, and the workflow's independence rules apply: an approver who
owns the asset, or who recorded its development evidence, is refused with `409` and the
list of blockers.

## Endpoints

| Method | Path | Role |
|---|---|---|
| GET | `/health` | none |
| GET | `/me` | any |
| GET | `/assets`, `/assets/{ref}` | any |
| POST | `/assets` | developer, validator |
| PUT | `/assets/{ref}/classification` | validator |
| PUT | `/assets/{ref}/risk-assessment` | validator |
| GET | `/assets/{ref}/plan?stage=` | any |
| GET | `/assets/{ref}/evidence?control_id=` | any |
| POST | `/assets/{ref}/evidence` (multipart: `control_id`, `type`, `summary`, optional `file`) | developer, validator |
| POST | `/assets/{ref}/checks/{control_id}` (multipart: one CSV per dataset role, plus `target`, `score`, `features`, `params` as JSON) | developer, validator |
| GET | `/findings?asset=&open_only=`, `/findings/aging` | any |
| POST | `/assets/{ref}/findings` | validator |
| POST | `/findings/{id}/close` (`{"evidence_ids": [...]}`) | validator |
| POST | `/findings/{id}/accept` | approver |
| GET | `/assets/{ref}/status` | any |
| POST | `/assets/{ref}/transitions` (`{"status": ..., "limits": ..., "note": ...}`) | approver for approval decisions; developer or validator otherwise |
| GET | `/assets/{ref}/reports/{template}?format=md\|docx` | any |
| GET | `/reports/inventory_report?format=md\|docx` | any |
| GET | `/audit/verify` | auditor |

`{ref}` is an asset id or its exact name (URL-encoded).

### Example

```bash
TOKEN=aimrg_...
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/assets/Card%20PD/status

curl -s -H "Authorization: Bearer $TOKEN" \
  -F baseline=@dev.csv -F current=@2026q3.csv \
  -F target=bad -F score=pd_score \
  http://127.0.0.1:8000/assets/Card%20PD/checks/FIN-MON-001
```

## Webhooks

AI-MRG makes no outbound calls unless an administrator adds a webhook:

```bash
aimrg webhook add https://hooks.example.com/aimrg --event finding.opened --event check.failed
aimrg webhook list
aimrg webhook test 3f2a9c1b
aimrg webhook remove 3f2a9c1b
```

Events: `finding.opened`, `finding.resolved`, `check.failed`, `lifecycle.transition`
(omit `--event` for all). They fire for changes made through the CLI, the API or Python.

Each delivery is a JSON `POST`:

```json
{"event": "check.failed", "sent_at": "2026-11-09T23:59:00+00:00",
 "data": {"pack": "fin-us-banking@0.1.0-dev", "asset": {"id": "...", "name": "Card PD"},
          "evidence": {...}}}
```

with headers `X-AIMRG-Event` and `X-AIMRG-Signature: sha256=<hex>`, an HMAC-SHA256 of the
raw body using the secret printed by `webhook add`. Verify it before trusting the payload:

```python
import hashlib, hmac

expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-AIMRG-Signature"])
```

Delivery is best-effort with a 5-second timeout. Failures are logged and never block the
change that triggered them. Use a receiver that can relay to Slack, Teams, email or a
ticketing system.
