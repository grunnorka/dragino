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
