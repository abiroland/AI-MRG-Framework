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

## Everything in containers (Docker Compose)

The repository includes a `Dockerfile` and `docker-compose.yml` for PostgreSQL, the API
and the dashboard:

```bash
export POSTGRES_PASSWORD='choose-a-strong-password'
docker compose up -d --build
docker compose exec api cat /data/bootstrap-admin-token   # admin API token, created once
```

Then open the dashboard at http://127.0.0.1:8501 and sign in with that token. Create
other users from the admin account:

```bash
docker compose exec api aimrg user add alice --role validator
```

- On first start the API container creates the registry (`aimrg init --if-missing`) and a
  bootstrap admin (`AIMRG_BOOTSTRAP_ADMIN`, default `admin`). Restarts reuse both.
- Data lives in the `db-data` (PostgreSQL) and `aimrg-data` (artifacts, bootstrap
  token) volumes. Back up both.
- Ports bind to `127.0.0.1`. To serve other machines, put a reverse proxy with TLS in
  front, and rotate the bootstrap token once real admin users exist
  (`aimrg user rotate-token admin`).
- The image runs as a non-root user. CI builds the stack and smoke-tests it on every
  change.

## Scheduled monitoring

`aimrg check run` exits with code 2 when a check fails, so it fits cron or a scheduler:

```bash
aimrg check run "Card Underwriting PD" FIN-MON-001 \
  --data baseline=dev.csv --data current=/data/scored/$(date +%Y-%m).csv \
  --target bad --score pd_score --actor monitoring-job
```

Combine with a `check.failed` webhook to notify the model owner.
