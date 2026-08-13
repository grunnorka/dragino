# Railway MQTT (Mosquitto) for Dragino

Project **dragino-mqtt** — service **mqtt** — environment **production**.

| Item | Value |
|------|--------|
| TCP proxy | `altaria.proxy.rlwy.net:33239` |
| Fallback IP | `66.33.22.220` (same port) |
| Username | `dragino` |
| Password | see `railway-mqtt.local.env` or Railway vars `MQTT_USER` / `MQTT_PASS` |
| TLS | Off |
| App port (inside container) | `1883` |
| Dashboard | https://railway.com/project/6275a0e4-fa40-4b5b-ae8c-67180378148e |

Public clients must use the **proxy host + proxy port** (`33239`), not `1883`.

## Public port 1883 (Railway limitation)

Railway **TCP Proxy cannot bind public port 1883** (or any chosen public port). Enabling a TCP proxy always yields a random high `*.proxy.rlwy.net:<port>` (docs + live proxies: `altaria:33239`, `hayabusa:24233`). A custom DNS CNAME can rename the host only; the **proxy port stays Railway-assigned**.

| Attempt | Result |
|---------|--------|
| `mqtt` TCP proxy → app 1883 | Public `altaria.proxy.rlwy.net:33239` (PS-CB OK) |
| `mqtt-tcp1883` socat → internal mqtt:1883 | Public `hayabusa.proxy.rlwy.net:24233` (still high port) |
| Local `shared/tcp_front_1883.py` on this PC | `127.0.0.1:1883` → altaria works (PC smoke OK); WAN `212.30.223.181:1883` **closed** (no router forward / firewall admin denied) |
| Fly.io / ngrok TCP front | CLI installed; **no auth token** (user AFK) — not deployed |

**Carrier proof (LTC2, 2026-08-07):** `SERVADDR=54.36.178.49,1883` (`test.mosquitto.org`), ATZ boot, eMTC `27402` BAND 20 — serial showed `Failed to open the MQTT client network` then later `Opened MQTT` → `Failed to send` (never `Successfully connected`). So **port 1883 alone does not clear LTC2 uplink** on this IMSI/path. Same fail class as high Railway ports. PS-CB on `:33239` still unaffected.

## Local files

| Path | Role |
|------|------|
| [`../mqtt/mosquitto/`](../mqtt/mosquitto/) | Dockerfile + entrypoint deployed to Railway |
| [`../dashboard/`](../dashboard/) | Fleet UI + MQTT ingest (Postgres) |
| [`../dashboard/railway_wire.sh`](../dashboard/railway_wire.sh) | CLI helper to create ingest/web vars |
| [`../shared/railway_mqtt.py`](../shared/railway_mqtt.py) | Shared settings + print Dragino AT set |
| [`../shared/mqtt_smoke_railway.py`](../shared/mqtt_smoke_railway.py) | Pub/sub round-trip smoke test |
| [`../shared/mqtt_listen_railway.py`](../shared/mqtt_listen_railway.py) | Subscribe `dragino/#` |
| [`../railway-mqtt.env.example`](../railway-mqtt.env.example) | Env template |
| `../railway-mqtt.local.env` | Local secrets (gitignored) |

## Quick checks

```powershell
cd C:\Users\Arnor\Downloads\dragino
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy railway-mqtt.env.example railway-mqtt.local.env   # then set MQTT_PASS
python shared/railway_mqtt.py
python shared/railway_mqtt.py --at --device-id ps-cb
python shared/mqtt_smoke_railway.py
python shared/mqtt_listen_railway.py
```

## Dragino AT (apply yourself)

Use **JSON MQTT** (`AT+PRO=3,5`), not ThingsBoard `3,3` (that rewrites SERVADDR toward HiveMQ).

```text
AT+PRO=3,5
AT+SERVADDR=altaria.proxy.rlwy.net,33239
AT+UNAME=dragino
AT+PWD=<MQTT_PASS>
AT+PUBTOPIC=dragino/<device-id>/up
AT+SUBTOPIC=dragino/<device-id>/down
AT+CLIENT=<device-id>
AT+MQOS=1
AT+TLSMOD=0,0
AT+BKDNS=1,0,66.33.22.220,33239
AT+TDC=180
```

Re-check `AT+SERVADDR=?` / `AT+BKDNS=?` after any `AT+PRO` — clear HiveMQ if it appears.

## Redeploy broker

```powershell
cd C:\Users\Arnor\Downloads\dragino\mqtt\mosquitto
railway link --project 6275a0e4-fa40-4b5b-ae8c-67180378148e --environment production --service mqtt
railway up --detach
```

Or from repo root via MCP/CLI deploy of the `mqtt/mosquitto/` directory to service `mqtt`.

Optional: attach a Railway volume at `/mosquitto/data` for persistence (passwordfile + retained state). Broker runs fine without it.

## Dashboard (ingest + web)

Read-only fleet UI + MQTT→Postgres ingest live in the same project. Code: [`../dashboard/`](../dashboard/).

| Service | Image | `SERVICE_MODE` | Notes |
|---------|-------|----------------|-------|
| `Postgres` | Railway plugin | — | Provides `DATABASE_URL` |
| `ingest` | `dashboard/` Dockerfile | `ingest` | Private MQTT `mqtt.railway.internal:1883` |
| `web` | `dashboard/` Dockerfile | `web` | Public HTTPS + HTTP Basic Auth |

### One-time Railway wiring

```bash
# From a machine with `railway login` against project dragino-mqtt
cd dashboard
railway link --project 6275a0e4-fa40-4b5b-ae8c-67180378148e --environment production

# Add managed Postgres (once)
railway add --database postgres

# Create empty services, then deploy this directory to each
railway add --service ingest
railway add --service web

# Shared / service variables (set MQTT_PASS / BASIC_AUTH_PASSWORD to real secrets)
railway variables --service ingest \
  --set "SERVICE_MODE=ingest" \
  --set "MQTT_HOST=mqtt.railway.internal" \
  --set "MQTT_PORT=1883" \
  --set "MQTT_USER=dragino" \
  --set "MQTT_PASS=<MQTT_PASS>" \
  --set "DEVICE_IDS=ps-cb,ltc2" \
  --set "STALE_AFTER_HOURS=24"

railway variables --service web \
  --set "SERVICE_MODE=web" \
  --set "BASIC_AUTH_USER=admin" \
  --set "BASIC_AUTH_PASSWORD=<DASHBOARD_PASSWORD>" \
  --set "DEVICE_IDS=ps-cb,ltc2" \
  --set "STALE_AFTER_HOURS=24" \
  --set "MESSAGES_PER_DEVICE=50" \
  --set "REFRESH_SECONDS=60"

# Reference DATABASE_URL from the Postgres plugin into ingest + web
# (Railway UI: Variable → Add reference → Postgres.DATABASE_URL)
# or: railway variables --service ingest --set "DATABASE_URL=${{Postgres.DATABASE_URL}}"

railway up --service ingest --detach
railway up --service web --detach
railway domain --service web   # generate public HTTPS hostname
```

Ingest must **not** use the public TCP proxy; web never opens MQTT. Ops UX / downlinks are out of v1.

### Local smoke

```bash
export DATABASE_URL=postgresql://dragino:dragino@127.0.0.1:5432/dragino
export BASIC_AUTH_PASSWORD=devpass
export MQTT_HOST=altaria.proxy.rlwy.net MQTT_PORT=33239
export MQTT_USER=dragino MQTT_PASS=<MQTT_PASS>
pip install -r dashboard/requirements.txt
PYTHONPATH=. python -m dashboard.ingest
PYTHONPATH=. uvicorn dashboard.web:app --port 8000
```
