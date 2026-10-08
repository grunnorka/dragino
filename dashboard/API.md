# Telemetry data + API contract (v1)

The `dragino-mqtt` Railway project stores every uplink and serves the data as a
JSON API. The `web` service hosts the API (`/api/v1/*`, bearer token) next to the
fleet/admin UI (HTML, Basic Auth). grunnorkaDashboard calls the API **server
side** only.

Units keep sending raw values (mA, V). Scaling to engineering units happens at
read time from the `sensors` calibration table, so a calibration change
recomputes history. Nothing is stored scaled.

## 1. Schema (Postgres; additive and idempotent, applied by `ensure_schema`)

Existing tables `devices` and `uplinks` stay; these are added:

```sql
ALTER TABLE devices ADD COLUMN IF NOT EXISTS label TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS imei  TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS model TEXT;   -- slug: ps-cb | ltc2 | ...

ALTER TABLE uplinks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'uplink';
    -- 'uplink'  sensor data (has idc_input / vdc_input / channelN_temp)
    -- 'dl_ack'  {"IMEI":..,"Downklink_Ack":"success|error|reverted"[,"Error":..]}
    -- 'ota'     {"IMEI":..,"OTA":"downloaded|applied|rolled back|failed|restored","Version":..[,"Info":..]}
    -- 'status'  {"IMEI":..,"Image Version":..,"NB-IoT Stack":..,"Model":..}  (Event:Status reply)
    -- 'other'   anything else (raw/non-JSON etc.)
ALTER TABLE uplinks ADD COLUMN IF NOT EXISTS fw_version TEXT;  -- "Version" / "Image Version" when present
CREATE INDEX IF NOT EXISTS uplinks_device_kind_received_idx
    ON uplinks (device_id, kind, received_at DESC);

CREATE TABLE IF NOT EXISTS readings (
    device_id TEXT NOT NULL REFERENCES devices(id),
    t         TIMESTAMPTZ NOT NULL,          -- sample time (UTC)
    source    TEXT NOT NULL CHECK (source IN ('uplink', 'clocklog')),
    idc_ma    DOUBLE PRECISION,              -- 4-20 mA input
    vdc_v     DOUBLE PRECISION,              -- 0-30 V input
    temp1_c   DOUBLE PRECISION,              -- LTC2-CB channel1_temp
    temp2_c   DOUBLE PRECISION,              -- LTC2-CB channel2_temp
    uplink_id BIGINT REFERENCES uplinks(id) ON DELETE SET NULL,
    PRIMARY KEY (device_id, t)
);
-- insert with ON CONFLICT (device_id, t) DO NOTHING: a clock-log sample that is
-- repeated in the next uplink is stored once.

CREATE TABLE IF NOT EXISTS sensors (            -- calibration periods
    id         BIGSERIAL PRIMARY KEY,
    device_id  TEXT NOT NULL REFERENCES devices(id),
    valid_from TIMESTAMPTZ NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'idc' CHECK (channel IN ('idc', 'vdc')),
    kind       TEXT NOT NULL DEFAULT 'level',  -- level | pressure | flow | other
    unit       TEXT NOT NULL,                  -- m, kPa, bar, l/s ...
    in_low     DOUBLE PRECISION NOT NULL DEFAULT 4,    -- mA (or V for vdc)
    in_high    DOUBLE PRECISION NOT NULL DEFAULT 20,
    range_low  DOUBLE PRECISION NOT NULL,
    range_high DOUBLE PRECISION NOT NULL,
    "offset"   DOUBLE PRECISION NOT NULL DEFAULT 0,    -- e.g. mounting depth
    label      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (device_id, valid_from),
    CHECK (in_high <> in_low)
);
```

View `readings_scaled`: every `readings` row joined (LATERAL, latest
`valid_from <= t`) to its `sensors` row:

- `raw` = `idc_ma` when `channel = 'idc'`, `vdc_v` when `'vdc'`
- `value = range_low + (raw - in_low) / (in_high - in_low) * (range_high - range_low) + offset`
  (NULL when no sensor row applies or raw is NULL)
- `unit`, `kind`, `sensor_id`
- `quality` (from `idc_ma`, NAMUR NE 43), in this order:
  - `'no_signal'`: `idc_ma` < 0.5 (0.000 = no sensor / broken loop)
  - `'fault'`: < 3.6 or > 21.0
  - `'saturated'`: < 3.8 or > 20.5
  - `'ok'`: otherwise
  - NULL when `idc_ma` is NULL

Reading time rules (ingest):

- uplink sample: payload `time`; fall back to `received_at` when missing or before 2020-01-01
- clock-log samples: keys `"1"`, `"2"`, ... → `[idc_mA, vdc_V, "time"]` (probe variants: `[idc_mA, vdc_V, converted, "time"]`; the time is the last element); skip entries with a missing/pre-2020 time
- an uplink with no sensor values stores no `uplink` reading row
- LTC2 temperatures are stored raw; the API serves values <= -300 (sentinels -327.6 probe open, -983.0 converter missing) as null

Device identity rules (ingest):

- `{model-slug}-{IMEI}` when the payload has `Model`
- a message **without** `Model` but with `IMEI` maps to the existing device whose `imei` = that IMEI (or id ending in `-{IMEI}`), else the bare IMEI as before

## 2. Auth

- `Authorization: Bearer <token>`
- Railway variables on `web`:
  - `API_TOKEN_RW`: read + write
  - `API_TOKEN_RO` (optional): read only
- Compare with `secrets.compare_digest`
- Missing/invalid → 401; read-only token on a write route → 403
- Error body: `{"detail": "..."}`

## 3. Endpoints (JSON; all times ISO 8601 UTC with `Z`)

`GET /api/v1/health` (no auth) → `{"status":"ok","db":"ok","last_uplink_at":"...|null"}`; 503 when the DB query fails.

`GET /api/v1/devices` → `{"devices": [Device, ...]}`

`GET /api/v1/devices/{device_id}` → `Device` (404 unknown)

```jsonc
Device = {
  "id": "ps-cb-869181074164029",
  "label": "Bench unit" | null,
  "model": "ps-cb" | null,
  "imei": "869181074164029" | null,
  "first_seen_at": "...", "last_seen_at": "...",
  "status": "ok" | "stale" | "never-seen",       // STALE_AFTER_HOURS
  "battery_v": 3.512 | null, "signal_csq": 13 | null,   // latest kind='uplink'
  "fw_version": "openfw-0.3.2" | null,          // newest of: status rows, uplinks with a version, ota rows with result applied|restored
  "last_ota": {"t": "...", "result": "applied", "version": "openfw-0.3.2", "info": null} | null,
  "sensor": Sensor | null,                      // currently valid calibration
  "latest": Reading | null                      // newest readings_scaled row
}
Sensor  = {"id": 1, "valid_from": "...", "channel": "idc", "kind": "level", "unit": "m",
           "in_low": 4, "in_high": 20, "range_low": 0, "range_high": 10, "offset": 0, "label": null}
Reading = {"t": "...", "source": "uplink"|"clocklog", "idc_ma": 3.962, "vdc_v": 0.0,
           "temp1_c": null, "temp2_c": null,
           "value": 0.0 | null, "unit": "m" | null, "quality": "ok" | ... | null}
```

`GET /api/v1/devices/{id}/readings?from=&to=&bucket=raw|1h|1d&limit=&cursor=`

- defaults: `to` = now, `from` = `to` - 7 days, `bucket` = `raw`, `limit` = 5000 (max 20000)
- sort: ascending by `t`
- `raw` → `{"device_id", "bucket":"raw", "readings":[Reading...], "next_cursor": "<t of last row>"|null}`
  - `next_cursor` is set when `limit` was hit; pass it back as `cursor` (exclusive lower bound)
- `1h` / `1d` → `{"device_id", "bucket", "readings":[{"t": bucket start, "n", "idc_ma" (avg), "vdc_v" (avg), "value" (avg), "value_min", "value_max", "unit", "quality" (worst in bucket: no_signal > fault > saturated > ok)}], "next_cursor": null}`
  - grouped by bucket and sensor, so a calibration change mid-bucket never mixes units

`GET /api/v1/devices/{id}/events?kind=ota|dl_ack|status&limit=50` → `{"events":[{"id", "t", "kind", "result", "version", "info", "payload"}]}`, newest first

- `dl_ack`: `result` = `Downklink_Ack` value, `info` = `Error`
- `ota`: `result` = `OTA` value, `version` = `Version`, `info` = `Info`
- `status`: `version` = `Image Version`

`PATCH /api/v1/devices/{id}` (write) body `{"label": "..."|null}` → `Device`

`GET /api/v1/devices/{id}/sensors` → `{"sensors":[Sensor...]}`, newest `valid_from` first

`POST /api/v1/devices/{id}/sensors` (write)

- body: Sensor fields without `id`; `valid_from` defaults to now; `channel`/`in_low`/`in_high`/`offset` have the defaults above
- → 201 Sensor
- 409 when the same `valid_from` already exists
- 422 on validation errors (`in_high == in_low`, unknown channel, empty unit)

`PUT /api/v1/devices/{id}/sensors/{sensor_id}` (write) → Sensor

`DELETE /api/v1/devices/{id}/sensors/{sensor_id}` (write) → 204

Implementation notes (v1):

- `next_cursor` is set only when more rows actually follow
- `1h`/`1d`: `limit` caps rows, `cursor` is ignored, `next_cursor` is always null
- 422 bodies are FastAPI's standard `{"detail": [ {loc, msg, type}, ... ]}` (a list, not a string)
- `PUT` sensor: omitted `valid_from` keeps the existing value; everything else is a full replace
- health 503 body: `{"status":"error","db":"error","last_uplink_at":null,"detail":"database unavailable"}`
- any route returns 503 `{"detail":"database unavailable"}` when the DB is unreachable

OpenAPI: `/api/openapi.json`, docs at `/api/docs` (API routes only; HTML routes
are `include_in_schema=False`).

## 4. Ownership

| File | Owner |
|---|---|
| `ingest.py`, `extract.py`, `db.py` (schema + writes), `migrate.py` (backfill) | ingest work |
| `api.py`, `queries.py` (reads), `web.py`, `templates/` | API + UI work |
| grunnorkaDashboard | consumes §3 with `DRAGINO_API_URL` + `DRAGINO_API_TOKEN` |
