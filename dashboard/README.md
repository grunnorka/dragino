# Dragino fleet dashboard (v1)

Read-only observability UI + MQTT→Postgres ingest for the Railway Mosquitto broker.

## Services

| Service | Role | Start |
|---------|------|-------|
| `ingest` | Subscribe `dragino/+/up`, write Postgres | `SERVICE_MODE=ingest` |
| `web` | Basic Auth fleet UI | `SERVICE_MODE=web` (default) |

Both use this directory’s Dockerfile. Set `SERVICE_MODE` per Railway service.

## Env vars

| Var | Used by | Notes |
|-----|---------|-------|
| `DATABASE_URL` | both | Railway Postgres plugin |
| `MQTT_USER` / `MQTT_PASS` | ingest | Same as Mosquitto |
| `MQTT_HOST` / `MQTT_PORT` | ingest | Production: `mqtt.railway.internal` / `1883` |
| `MQTT_TOPIC` | ingest | Default `dragino/+/up` |
| `DEVICE_IDS` | both | Optional seed placeholders. Default empty — devices appear as `{model}-{IMEI}` on first JSON uplink |
| `STALE_AFTER_HOURS` | web | Default `24` |
| `BASIC_AUTH_USER` | web | Default `admin` |
| `BASIC_AUTH_PASSWORD` | web | Required |
| `MESSAGES_PER_DEVICE` | web | Default `50` |
| `REFRESH_SECONDS` | web | Default `60` |
| `SERVICE_MODE` | runtime | `ingest` or `web` |
| `PORT` | web | Railway sets this |

## Local run

```bash
# Postgres (example)
export DATABASE_URL=postgresql://dragino:dragino@127.0.0.1:5432/dragino
export BASIC_AUTH_PASSWORD=devpass
export MQTT_HOST=altaria.proxy.rlwy.net MQTT_PORT=33239
export MQTT_USER=dragino MQTT_PASS=...

pip install -r dashboard/requirements.txt
PYTHONPATH=. python -m dashboard.ingest   # terminal 1
PYTHONPATH=. uvicorn dashboard.web:app --reload --port 8000  # terminal 2
```

Open http://127.0.0.1:8000 — browser prompts for Basic Auth.

## Railway

See [docs/RAILWAY_MQTT.md](../docs/RAILWAY_MQTT.md) § Dashboard (ingest + web).
