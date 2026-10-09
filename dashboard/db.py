"""Postgres schema helpers and queries (sync psycopg)."""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from dashboard.extract import (
    Reading,
    classify_kind,
    extract_common,
    extract_readings,
    model_slug,
    resolve_device_id,
)

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


def connect(database_url: str, *, timeouts: bool = True) -> psycopg.Connection:
    """Open a connection.

    ``timeouts`` adds a connect timeout and TCP keepalives / user timeout, so a
    silently dropped link surfaces as OperationalError instead of hanging.
    """
    # Railway sometimes provides postgres:// — normalize for psycopg
    url = database_url.replace("postgres://", "postgresql://", 1)
    extra: dict[str, Any] = {}
    if timeouts:
        extra = {
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "tcp_user_timeout": 30000,
        }
    return psycopg.connect(url, row_factory=dict_row, **extra)


def ensure_schema(
    conn: psycopg.Connection, device_ids: tuple[str, ...], *, commit: bool = True
) -> None:
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
    if commit:
        conn.commit()


def upsert_device_auto(
    conn: psycopg.Connection,
    device_id: str,
    seen_at: datetime,
    *,
    imei: str | None = None,
    model: str | None = None,
) -> None:
    """Create/touch a device. ``imei`` / ``model`` never overwrite known values."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO devices (id, first_seen_at, last_seen_at, created_via, imei, model)
            VALUES (%s, %s, %s, 'auto', %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                last_seen_at = GREATEST(devices.last_seen_at, EXCLUDED.last_seen_at),
                first_seen_at = LEAST(devices.first_seen_at, EXCLUDED.first_seen_at),
                imei = COALESCE(EXCLUDED.imei, devices.imei),
                model = COALESCE(EXCLUDED.model, devices.model)
            """,
            (device_id, seen_at, seen_at, imei, model),
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


def find_device_by_imei(conn: psycopg.Connection, imei: str) -> str | None:
    """Existing device for an IMEI: ``imei`` column or id ``…-{IMEI}``.

    A ``{model}-{IMEI}`` id wins over the bare-IMEI row, then the most recently
    seen device.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM devices
            WHERE imei = %(imei)s::text OR right(id, length(%(imei)s::text) + 1) = '-' || %(imei)s::text
            ORDER BY (id = %(imei)s::text), last_seen_at DESC NULLS LAST, id
            LIMIT 1
            """,
            {"imei": imei},
        )
        row = cur.fetchone()
    return row["id"] if row else None


def resolve_device(
    conn: psycopg.Connection, topic: str, extracts: dict[str, Any]
) -> str | None:
    """Device id for a message (API.md §1 identity rules).

    * ``Model`` + ``IMEI`` -> ``{slug}-{IMEI}``
    * ``IMEI`` only (acks, OTA reports) -> the existing device for that IMEI,
      else the bare IMEI
    * no IMEI -> the topic segment (legacy)
    """
    imei = extracts.get("imei")
    if imei and not model_slug(extracts.get("model")):
        found = find_device_by_imei(conn, imei)
        if found:
            return found
    return resolve_device_id(topic, extracts)


def rehome_device(cur: psycopg.Cursor, bare_id: str, target_id: str) -> dict[str, int]:
    """Move everything of ``bare_id`` onto ``target_id``; drop the bare row if empty.

    Counts: uplinks / readings / sensors moved, ``deleted`` (0|1). Readings and
    sensors that would collide with the target's (same ``t`` / ``valid_from``)
    stay behind; the bare row is then kept (and reported), never forced away.
    """
    counts = {"uplinks": 0, "readings": 0, "sensors": 0, "deleted": 0}
    if bare_id == target_id:
        return counts
    cur.execute("UPDATE uplinks SET device_id = %s WHERE device_id = %s", (target_id, bare_id))
    counts["uplinks"] = cur.rowcount
    # identical (device, t) key -> the target's row already holds that sample
    cur.execute(
        """
        DELETE FROM readings b USING readings t
        WHERE b.device_id = %s AND t.device_id = %s AND t.t = b.t
        """,
        (bare_id, target_id),
    )
    cur.execute("UPDATE readings SET device_id = %s WHERE device_id = %s", (target_id, bare_id))
    counts["readings"] = cur.rowcount
    cur.execute(
        """
        UPDATE sensors s SET device_id = %s
        WHERE s.device_id = %s
          AND NOT EXISTS (SELECT 1 FROM sensors x
                          WHERE x.device_id = %s AND x.valid_from = s.valid_from)
        """,
        (target_id, bare_id, target_id),
    )
    counts["sensors"] = cur.rowcount
    cur.execute(
        """
        UPDATE devices t SET
            first_seen_at = LEAST(t.first_seen_at, b.first_seen_at),
            last_seen_at = GREATEST(t.last_seen_at, b.last_seen_at),
            label = COALESCE(t.label, b.label),
            imei = COALESCE(t.imei, b.imei),
            model = COALESCE(t.model, b.model)
        FROM devices b WHERE t.id = %s AND b.id = %s
        """,
        (target_id, bare_id),
    )
    cur.execute(
        """
        DELETE FROM devices d WHERE d.id = %s
          AND NOT EXISTS (SELECT 1 FROM uplinks WHERE device_id = d.id)
          AND NOT EXISTS (SELECT 1 FROM readings WHERE device_id = d.id)
          AND NOT EXISTS (SELECT 1 FROM sensors WHERE device_id = d.id)
        """,
        (bare_id,),
    )
    counts["deleted"] = cur.rowcount
    return counts


def insert_uplink(
    conn: psycopg.Connection,
    *,
    device_id: str,
    topic: str,
    received_at: datetime,
    payload: dict[str, Any],
    extracts: dict[str, Any],
) -> int:
    """Insert the raw message; returns ``uplinks.id``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO uplinks (
                device_id, topic, received_at, payload,
                battery, signal, imei, model, device_time, kind, fw_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
                extracts.get("kind") or classify_kind(payload),
                extracts.get("fw_version"),
            ),
        )
        row = cur.fetchone()
    return int(row["id"])


def insert_readings(
    conn: psycopg.Connection,
    device_id: str,
    uplink_id: int | None,
    readings: Iterable[Reading],
) -> int:
    """Insert readings, skipping existing ``(device_id, t)``; returns rows inserted."""
    inserted = 0
    with conn.cursor() as cur:
        for r in readings:
            cur.execute(
                """
                INSERT INTO readings
                    (device_id, t, source, idc_ma, vdc_v, temp1_c, temp2_c, uplink_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (device_id, t) DO NOTHING
                """,
                (device_id, r.t, r.source, r.idc_ma, r.vdc_v, r.temp1_c, r.temp2_c, uplink_id),
            )
            inserted += cur.rowcount
    return inserted


@dataclass(frozen=True)
class Stored:
    device_id: str
    kind: str
    uplink_id: int
    readings: int


def _store(
    conn: psycopg.Connection,
    *,
    device_id: str,
    topic: str,
    received_at: datetime,
    payload: dict[str, Any],
    extracts: dict[str, Any],
) -> Stored:
    """Device upsert + uplink row + readings. No commit."""
    imei = extracts.get("imei")
    slug = model_slug(extracts.get("model"))
    upsert_device_auto(conn, device_id, received_at, imei=imei, model=slug)
    if imei and device_id != imei:
        # a bare-IMEI row made by an earlier model-less message -> fold it in
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM devices WHERE id = %s", (imei,))
            if cur.fetchone():
                rehome_device(cur, imei, device_id)
    uplink_id = insert_uplink(
        conn,
        device_id=device_id,
        topic=topic,
        received_at=received_at,
        payload=payload,
        extracts=extracts,
    )
    kind = extracts.get("kind") or classify_kind(payload)
    n = 0
    if kind == "uplink":
        n = insert_readings(conn, device_id, uplink_id, extract_readings(payload, received_at))
    return Stored(device_id=device_id, kind=kind, uplink_id=uplink_id, readings=n)


def store_message(
    conn: psycopg.Connection,
    *,
    topic: str,
    received_at: datetime,
    payload: dict[str, Any],
) -> Stored | None:
    """Persist one MQTT message in a single transaction.

    Resolves the device (see :func:`resolve_device`), upserts it, stores the raw
    uplink row and its readings. Returns ``None`` when no device id can be
    derived (nothing is written). Rolls back and re-raises on any error.
    """
    try:
        extracts = extract_common(payload)
        device_id = resolve_device(conn, topic, extracts)
        if not device_id:
            conn.rollback()
            return None
        stored = _store(
            conn,
            device_id=device_id,
            topic=topic,
            received_at=received_at,
            payload=payload,
            extracts=extracts,
        )
        conn.commit()
        return stored
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def record_uplink(
    conn: psycopg.Connection,
    *,
    device_id: str,
    topic: str,
    received_at: datetime,
    payload: dict[str, Any],
    extracts: dict[str, Any],
    known_seed: bool = False,
) -> int:
    """Store a message under an already-resolved ``device_id``; returns readings stored.

    ``known_seed`` is kept for compatibility: the upsert treats seed and auto
    devices the same (``created_via`` is only set on first insert).
    """
    del known_seed
    try:
        stored = _store(
            conn,
            device_id=device_id,
            topic=topic,
            received_at=received_at,
            payload=payload,
            extracts=extracts,
        )
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return stored.readings


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
