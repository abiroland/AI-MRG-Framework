# Deployment

## Single user: SQLite (default)

```bash
aimrg init                       # creates aimrg.db and aimrg-artifacts/ next to it
```

Back up the `.db` file and the artifacts folder together.

## Teams: PostgreSQL

```bash
pip install -e ".[postgres,api]"
export AIMRG_DB="postgresql://aimrg:<password>@db.internal:5432/aimrg"
aimrg init --artifacts-dir /srv/aimrg/artifacts
aimrg user add mrm-admin --role admin
aimrg serve --host 127.0.0.1 --port 8000
```

- The database user needs to create tables, functions and triggers once (at `init`), and
  read/write afterwards.
- Evidence and lifecycle events are protected by `BEFORE UPDATE OR DELETE` triggers. To
  make that guard stronger, run day-to-day access as a role that does not own the tables,
  so it cannot drop the triggers. Tamper *evidence* still comes from the hash chain:
  schedule `aimrg audit verify` (or `GET /audit/verify`) and alert on failure.
- The artifacts directory must be reachable by every process that writes or reads
  evidence (a shared volume). Artifacts are content-addressed and verified by hash.
- Put credentials in `AIMRG_DB` or a secrets manager, not on the command line; error
  messages hide the password part of the URL.

## Scheduled monitoring

`aimrg check run` exits with code 2 when a check fails, so it fits cron or a scheduler:

```bash
aimrg check run "Card Underwriting PD" FIN-MON-001 \
  --data baseline=dev.csv --data current=/data/scored/$(date +%Y-%m).csv \
  --target bad --score pd_score --actor monitoring-job
```

Combine with a `check.failed` webhook to notify the model owner.
