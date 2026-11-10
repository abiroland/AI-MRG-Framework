#!/bin/sh
# Entrypoint for the AI-MRG container.
#   api        initialize the registry if needed, create a bootstrap admin token once,
#              then serve the REST API
#   dashboard  run the Streamlit dashboard against $AIMRG_API_URL
#   <other>    run any aimrg command, e.g. `user add alice --role validator`
set -eu

: "${AIMRG_DB:?set AIMRG_DB (a postgresql:// URL or a SQLite path)}"
ARTIFACTS="${AIMRG_ARTIFACTS_DIR:-/data/artifacts}"

case "${1:-api}" in
  api)
    aimrg init --if-missing --artifacts-dir "$ARTIFACTS"
    if [ -n "${AIMRG_BOOTSTRAP_ADMIN:-}" ] && [ ! -f /data/bootstrap-admin-token ]; then
      aimrg user add "$AIMRG_BOOTSTRAP_ADMIN" --role admin | tail -n 1 > /data/bootstrap-admin-token
      chmod 600 /data/bootstrap-admin-token
      echo "Bootstrap admin '$AIMRG_BOOTSTRAP_ADMIN' created; token saved in the data volume"
      echo "Read it with: docker compose exec api cat /data/bootstrap-admin-token"
    fi
    exec aimrg serve --host 0.0.0.0 --port 8000
    ;;
  dashboard)
    exec aimrg dashboard --api-url "${AIMRG_API_URL:-http://api:8000}" --port 8501
    ;;
  *)
    exec aimrg "$@"
    ;;
esac
