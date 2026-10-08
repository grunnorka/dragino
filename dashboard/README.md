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
| `INGEST_MAX_DB_FAILURES` | ingest | Default `5`. Consecutive failed persists (after a reconnect + retry) before the worker logs ERROR and exits 1 |
| `INGEST_STALL_SECONDS` | ingest | Default `1800`. Exit 1 when persists have been failing this long while messages keep arriving |
| `STALE_AFTER_HOURS` | web | Default `24` |
| `BASIC_AUTH_USER` | web | Default `admin` |
| `BASIC_AUTH_PASSWORD` | web | Required |
| `MESSAGES_PER_DEVICE` | web | Default `50` |
| `REFRESH_SECONDS` | web | Default `60` |
| `SERVICE_MODE` | runtime | `ingest` or `web` |
| `PORT` | web | Railway sets this |

## Ingest reliability

The worker keeps one Postgres connection and heals it: it reconnects when the
connection is closed or raises `OperationalError`/`InterfaceError` and retries
the message once; after 5 minutes without a DB operation it runs `SELECT 1`
first. If persisting still fails `INGEST_MAX_DB_FAILURES` times in a row, or
keeps failing for `INGEST_STALL_SECONDS`, it logs `ERROR fatal: ...` and exits
with code 1, so Railway's `ON_FAILURE` restart policy starts a fresh process.
Each stored message logs `INFO uplink device=… kind=… readings=…`; no such line
for a while while the broker is busy means something is wrong.

## Backfill

After deploying a new version (and for old data), run the idempotent backfill
against the target database, **dry run first** (it does everything in a
transaction and rolls back, printing the counts):

```bash
PYTHONPATH=. python -m dashboard.migrate --dry-run
PYTHONPATH=. python -m dashboard.migrate
```

It applies the schema, sets `uplinks.kind` / `fw_version` from each payload,
fills `devices.imei` / `model`, folds bare-IMEI devices into their
`{model}-{IMEI}` device and backfills `readings` from stored uplinks. Running it
again changes nothing. `DATABASE_URL` is read from the environment and never
printed.

## Tests

```bash
podman start telemetry-pg   # local Postgres on 127.0.0.1:55432 (database dragino_ingest)
PYTHONPATH=. python -m pytest dashboard/tests -q
```

DB tests wipe their tables; point `TEST_DATABASE_URL` only at a throw-away
database whose name contains `ingest` or `test`. They skip when it is
unreachable (`REQUIRE_DB=1` makes that a failure).

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
