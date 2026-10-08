"""Postgres schema helpers and queries (sync psycopg)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    created_via TEXT NOT NULL CHECK (created_via IN ('seed', 'auto'))
);

CREATE TABLE IF NOT EXISTS uplinks (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    topic TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL,
    battery DOUBLE PRECISION,
    signal DOUBLE PRECISION,
    imei TEXT,
    model TEXT,
    device_time TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS uplinks_device_received_idx
    ON uplinks (device_id, received_at DESC);

-- v1 telemetry (see API.md §1)
ALTER TABLE devices ADD COLUMN IF NOT EXISTS label TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS imei TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS model TEXT;

ALTER TABLE uplinks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'uplink';
ALTER TABLE uplinks ADD COLUMN IF NOT EXISTS fw_version TEXT;
CREATE INDEX IF NOT EXISTS uplinks_device_kind_received_idx
    ON uplinks (device_id, kind, received_at DESC);

CREATE TABLE IF NOT EXISTS readings (
    device_id TEXT NOT NULL REFERENCES devices(id),
    t TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('uplink', 'clocklog')),
    idc_ma DOUBLE PRECISION,
    vdc_v DOUBLE PRECISION,
    temp1_c DOUBLE PRECISION,
    temp2_c DOUBLE PRECISION,
    uplink_id BIGINT REFERENCES uplinks(id) ON DELETE SET NULL,
    PRIMARY KEY (device_id, t)
);

CREATE TABLE IF NOT EXISTS sensors (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    valid_from TIMESTAMPTZ NOT NULL,
    channel TEXT NOT NULL DEFAULT 'idc' CHECK (channel IN ('idc', 'vdc')),
    kind TEXT NOT NULL DEFAULT 'level',
    unit TEXT NOT NULL,
    in_low DOUBLE PRECISION NOT NULL DEFAULT 4,
    in_high DOUBLE PRECISION NOT NULL DEFAULT 20,
    range_low DOUBLE PRECISION NOT NULL,
    range_high DOUBLE PRECISION NOT NULL,
    "offset" DOUBLE PRECISION NOT NULL DEFAULT 0,
    label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (device_id, valid_from),
    CHECK (in_high <> in_low)
);

CREATE OR REPLACE VIEW readings_scaled AS
SELECT
    r.device_id,
    r.t,
    r.source,
    r.idc_ma,
    r.vdc_v,
    r.temp1_c,
    r.temp2_c,
    r.uplink_id,
    s.id AS sensor_id,
    s.kind,
    s.unit,
    s.range_low
        + ((CASE s.channel WHEN 'vdc' THEN r.vdc_v ELSE r.idc_ma END) - s.in_low)
          / (s.in_high - s.in_low) * (s.range_high - s.range_low)
        + s."offset" AS value,
    CASE
        WHEN r.idc_ma IS NULL THEN NULL
        WHEN r.idc_ma < 0.5 THEN 'no_signal'
        WHEN r.idc_ma < 3.6 OR r.idc_ma > 21.0 THEN 'fault'
        WHEN r.idc_ma < 3.8 OR r.idc_ma > 20.5 THEN 'saturated'
        ELSE 'ok'
    END AS quality
FROM readings r
LEFT JOIN LATERAL (
    SELECT *
    FROM sensors
    WHERE sensors.device_id = r.device_id AND sensors.valid_from <= r.t
    ORDER BY sensors.valid_from DESC
    LIMIT 1
) s ON TRUE;
"""


def connect(database_url: str) -> psycopg.Connection:
    # Railway sometimes provides postgres:// — normalize for psycopg
    url = database_url.replace("postgres://", "postgresql://", 1)
    return psycopg.connect(url, row_factory=dict_row)


def ensure_schema(conn: psycopg.Connection, device_ids: tuple[str, ...]) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        for device_id in device_ids:
            cur.execute(
                """
                INSERT INTO devices (id, first_seen_at, last_seen_at, created_via)
                VALUES (%s, NULL, NULL, 'seed')
                ON CONFLICT (id) DO NOTHING
                """,
                (device_id,),
            )
    conn.commit()


def upsert_device_auto(conn: psycopg.Connection, device_id: str, seen_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO devices (id, first_seen_at, last_seen_at, created_via)
            VALUES (%s, %s, %s, 'auto')
            ON CONFLICT (id) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                first_seen_at = COALESCE(devices.first_seen_at, EXCLUDED.first_seen_at)
            """,
            (device_id, seen_at, seen_at),
        )


def touch_device(conn: psycopg.Connection, device_id: str, seen_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE devices
            SET last_seen_at = %s,
                first_seen_at = COALESCE(first_seen_at, %s)
            WHERE id = %s
            """,
            (seen_at, seen_at, device_id),
        )


def insert_uplink(
    conn: psycopg.Connection,
    *,
    device_id: str,
    topic: str,
    received_at: datetime,
    payload: dict[str, Any],
    extracts: dict[str, Any],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO uplinks (
                device_id, topic, received_at, payload,
                battery, signal, imei, model, device_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                device_id,
                topic,
                received_at,
                Jsonb(payload),
                extracts.get("battery"),
                extracts.get("signal"),
                extracts.get("imei"),
                extracts.get("model"),
                extracts.get("device_time"),
            ),
        )


def record_uplink(
    conn: psycopg.Connection,
    *,
    device_id: str,
    topic: str,
    received_at: datetime,
    payload: dict[str, Any],
    extracts: dict[str, Any],
    known_seed: bool,
) -> None:
    if known_seed:
        touch_device(conn, device_id, received_at)
        # If somehow missing (race), insert as seed-equivalent auto then touch
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM devices WHERE id = %s", (device_id,))
            if cur.fetchone() is None:
                upsert_device_auto(conn, device_id, received_at)
    else:
        upsert_device_auto(conn, device_id, received_at)
    insert_uplink(
        conn,
        device_id=device_id,
        topic=topic,
        received_at=received_at,
        payload=payload,
        extracts=extracts,
    )
    conn.commit()


def list_fleet(conn: psycopg.Connection, stale_after_hours: int) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_delta = timedelta(hours=stale_after_hours)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                d.id,
                d.first_seen_at,
                d.last_seen_at,
                d.created_via,
                u.battery,
                u.signal,
                u.model,
                u.imei,
                u.received_at AS last_uplink_at
            FROM devices d
            LEFT JOIN LATERAL (
                SELECT battery, signal, model, imei, received_at
                FROM uplinks
                WHERE device_id = d.id
                ORDER BY received_at DESC
                LIMIT 1
            ) u ON TRUE
            ORDER BY d.id
            """
        )
        rows = list(cur.fetchall())

    out: list[dict[str, Any]] = []
    for row in rows:
        last_seen = row["last_seen_at"] or row["last_uplink_at"]
        if last_seen is None:
            status = "never-seen"
            stale = True
        elif now - last_seen > stale_delta:
            status = "stale"
            stale = True
        else:
            status = "ok"
            stale = False
        out.append(
            {
                **row,
                "last_seen_at": last_seen,
                "status": status,
                "stale": stale,
            }
        )
    return out


def get_device(conn: psycopg.Connection, device_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, first_seen_at, last_seen_at, created_via FROM devices WHERE id = %s",
            (device_id,),
        )
        return cur.fetchone()


def list_uplinks(
    conn: psycopg.Connection, device_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, topic, received_at, payload, battery, signal, imei, model, device_time
            FROM uplinks
            WHERE device_id = %s
            ORDER BY received_at DESC
            LIMIT %s
            """,
            (device_id, limit),
        )
        rows = list(cur.fetchall())
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            row["payload"] = json.loads(payload)
        row["payload_pretty"] = json.dumps(row["payload"], indent=2, sort_keys=True)
    return rows
