#!/usr/bin/env bash
# Provision ingest + web (+ Postgres) in Railway project dragino-mqtt.
# Requires: railway login, then run from repo: bash dashboard/railway_wire.sh
set -euo pipefail

PROJECT_ID="6275a0e4-fa40-4b5b-ae8c-67180378148e"
ENV_NAME="production"
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "${MQTT_PASS:-}" || -z "${BASIC_AUTH_PASSWORD:-}" ]]; then
  echo "Set MQTT_PASS and BASIC_AUTH_PASSWORD in the environment before running." >&2
  exit 1
fi

command -v railway >/dev/null || { echo "railway CLI not found" >&2; exit 1; }
railway whoami >/dev/null

cd "$ROOT"
railway link --project "$PROJECT_ID" --environment "$ENV_NAME"

echo "Ensure Postgres plugin exists (skip if already added)…"
railway add --database postgres </dev/null || true

echo "Ensure ingest/web services exist…"
railway add --service ingest </dev/null || true
railway add --service web </dev/null || true

railway variables --service ingest \
  --set "SERVICE_MODE=ingest" \
  --set "MQTT_HOST=mqtt.railway.internal" \
  --set "MQTT_PORT=1883" \
  --set "MQTT_USER=dragino" \
  --set "MQTT_PASS=${MQTT_PASS}" \
  --set "DEVICE_IDS=ps-cb,ltc2" \
  --set "STALE_AFTER_HOURS=24"

railway variables --service web \
  --set "SERVICE_MODE=web" \
  --set "BASIC_AUTH_USER=admin" \
  --set "BASIC_AUTH_PASSWORD=${BASIC_AUTH_PASSWORD}" \
  --set "DEVICE_IDS=ps-cb,ltc2" \
  --set "STALE_AFTER_HOURS=24" \
  --set "MESSAGES_PER_DEVICE=50" \
  --set "REFRESH_SECONDS=60"

echo "Attach DATABASE_URL from Postgres to ingest + web in the Railway UI"
echo "(Variables → Add variable reference → Postgres → DATABASE_URL), then:"
echo "  cd dashboard && railway up --service ingest --detach"
echo "  cd dashboard && railway up --service web --detach"
echo "  railway domain --service web"
