#!/bin/sh
set -eu
MODE="${SERVICE_MODE:-web}"
case "$MODE" in
  ingest)
    exec python -m dashboard.ingest
    ;;
  web|*)
    exec uvicorn dashboard.web:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
esac
