"""Read/write SQL for the JSON API and the HTML UI (sync psycopg, dict rows).

Every function takes an open connection; the caller owns its lifecycle (one
connection per request, see api.db_conn). Datetimes come back timezone-aware;
use iso() / to_utc() at the edges.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation

from dashboard.db import connect

OTA_RESULT_KEY = "OTA"
TEMP_SENTINEL_MAX = -300.0  # LTC2 probe-open (-327.6) / converter-missing (-983.0)
EVENT_KINDS = ("ota", "dl_ack", "status")
QUALITY_RANK = {"ok": 1, "saturated": 2, "fault": 3, "no_signal": 4}
SENSOR_COLUMNS = (
    "id, valid_from, channel, kind, unit, in_low, in_high, "
    'range_low, range_high, "offset", label'
)


# --- helpers -----------------------------------------------------------------


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """ISO 8601 UTC with a trailing Z (microseconds kept: cursors round-trip)."""
    if value is None:
        return None
    return to_utc(value).isoformat().replace("+00:00", "Z")


def open_conn(database_url: str) -> psycopg.Connection:
    """Open a fresh connection (db.connect adds connect timeout + keepalives)."""
    return connect(database_url)


def device_status(last_seen: datetime | None, stale_after_hours: int) -> str:
    if last_seen is None:
        return "never-seen"
    age = datetime.now(timezone.utc) - to_utc(last_seen)
    return "stale" if age > timedelta(hours=stale_after_hours) else "ok"


def _num(value: Any) -> Any:
    """Whole floats as ints (CSQ is stored as double)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _temp(value: float | None) -> float | None:
    """Sentinel temperatures are served as null (raw stays in the DB)."""
    return None if value is not None and value <= TEMP_SENTINEL_MAX else value


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


# --- liveness ------------------------------------------------------------------


def last_uplink_at(conn: psycopg.Connection) -> datetime | None:
    """Newest uplink in the DB (index-only per device; also proves the DB works)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(u.received_at) AS t
            FROM devices d
            CROSS JOIN LATERAL (
                SELECT received_at FROM uplinks
                WHERE device_id = d.id
                ORDER BY received_at DESC LIMIT 1
            ) u
            """
        )
        row = cur.fetchone()
    return row["t"] if row else None


# --- devices -------------------------------------------------------------------

_DEVICE_SQL = """
SELECT
    d.id,
    d.label,
    d.created_via,
    COALESCE(d.model, lm.model) AS model,
    COALESCE(d.imei, lm.imei) AS imei,
    d.first_seen_at,
    COALESCE(d.last_seen_at, la.received_at) AS last_seen_at,
    lu.battery,
    lu."signal" AS signal,
    lf.fw_version,
    lo.received_at AS ota_t,
    lo.payload AS ota_payload,
    ls.id AS s_id, ls.valid_from AS s_valid_from, ls.channel AS s_channel,
    ls.kind AS s_kind, ls.unit AS s_unit, ls.in_low AS s_in_low,
    ls.in_high AS s_in_high, ls.range_low AS s_range_low,
    ls.range_high AS s_range_high, ls."offset" AS s_offset, ls.label AS s_label,
    lr.t AS r_t, lr.source AS r_source, lr.idc_ma AS r_idc_ma, lr.vdc_v AS r_vdc_v,
    lr.temp1_c AS r_temp1_c, lr.temp2_c AS r_temp2_c, lr.value AS r_value,
    lr.unit AS r_unit, lr.quality AS r_quality
FROM devices d
LEFT JOIN LATERAL (
    SELECT model, imei FROM uplinks
    WHERE device_id = d.id AND (model IS NOT NULL OR imei IS NOT NULL)
    ORDER BY received_at DESC LIMIT 1
) lm ON TRUE
LEFT JOIN LATERAL (
    SELECT received_at FROM uplinks
    WHERE device_id = d.id
    ORDER BY received_at DESC LIMIT 1
) la ON TRUE
LEFT JOIN LATERAL (
    SELECT battery, "signal" FROM uplinks
    WHERE device_id = d.id AND kind = 'uplink'
    ORDER BY received_at DESC LIMIT 1
) lu ON TRUE
LEFT JOIN LATERAL (
    SELECT fw_version FROM uplinks
    WHERE device_id = d.id AND fw_version IS NOT NULL
      AND (kind IN ('status', 'uplink')
           OR (kind = 'ota' AND payload->>'OTA' IN ('applied', 'restored')))
    ORDER BY received_at DESC LIMIT 1
) lf ON TRUE
LEFT JOIN LATERAL (
    SELECT received_at, payload FROM uplinks
    WHERE device_id = d.id AND kind = 'ota'
    ORDER BY received_at DESC LIMIT 1
) lo ON TRUE
LEFT JOIN LATERAL (
    SELECT * FROM sensors
    WHERE device_id = d.id AND valid_from <= NOW()
    ORDER BY valid_from DESC LIMIT 1
) ls ON TRUE
LEFT JOIN LATERAL (
    SELECT * FROM readings_scaled
    WHERE device_id = d.id
    ORDER BY t DESC LIMIT 1
) lr ON TRUE
{where}
ORDER BY d.id
"""


def _shape_device(row: dict[str, Any], stale_after_hours: int) -> dict[str, Any]:
    ota_payload = _payload(row["ota_payload"])
    last_ota = None
    if row["ota_t"] is not None:
        last_ota = {
            "t": row["ota_t"],
            "result": ota_payload.get(OTA_RESULT_KEY),
            "version": ota_payload.get("Version"),
            "info": ota_payload.get("Info"),
        }
    sensor = None
    if row["s_id"] is not None:
        sensor = {
            "id": row["s_id"],
            "valid_from": row["s_valid_from"],
            "channel": row["s_channel"],
            "kind": row["s_kind"],
            "unit": row["s_unit"],
            "in_low": row["s_in_low"],
            "in_high": row["s_in_high"],
            "range_low": row["s_range_low"],
            "range_high": row["s_range_high"],
            "offset": row["s_offset"],
            "label": row["s_label"],
        }
    latest = None
    if row["r_t"] is not None:
        latest = {
            "t": row["r_t"],
            "source": row["r_source"],
            "idc_ma": row["r_idc_ma"],
            "vdc_v": row["r_vdc_v"],
            "temp1_c": _temp(row["r_temp1_c"]),
            "temp2_c": _temp(row["r_temp2_c"]),
            "value": row["r_value"],
            "unit": row["r_unit"],
            "quality": row["r_quality"],
        }
    return {
        "id": row["id"],
        "label": row["label"],
        "model": row["model"],
        "imei": row["imei"],
        "created_via": row["created_via"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "status": device_status(row["last_seen_at"], stale_after_hours),
        "battery_v": row["battery"],
        "signal_csq": _num(row["signal"]),
        "fw_version": row["fw_version"],
        "last_ota": last_ota,
        "sensor": sensor,
        "latest": latest,
    }


def list_devices(conn: psycopg.Connection, stale_after_hours: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(_DEVICE_SQL.format(where=""))
        rows = list(cur.fetchall())
    return [_shape_device(r, stale_after_hours) for r in rows]


def get_device(
    conn: psycopg.Connection, device_id: str, stale_after_hours: int
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_DEVICE_SQL.format(where="WHERE d.id = %s"), (device_id,))
        row = cur.fetchone()
    return _shape_device(row, stale_after_hours) if row else None


def device_exists(conn: psycopg.Connection, device_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM devices WHERE id = %s", (device_id,))
        return cur.fetchone() is not None


def set_label(conn: psycopg.Connection, device_id: str, label: str | None) -> bool:
    with conn.cursor() as cur:
        cur.execute("UPDATE devices SET label = %s WHERE id = %s", (label, device_id))
        changed = cur.rowcount > 0
    conn.commit()
    return changed


# --- readings --------------------------------------------------------------------

_READING_COLUMNS = "t, source, idc_ma, vdc_v, temp1_c, temp2_c, value, unit, quality"
_BUCKET_TRUNC = {"1h": "hour", "1d": "day"}


def readings_raw(
    conn: psycopg.Connection,
    device_id: str,
    t_from: datetime,
    t_to: datetime,
    limit: int,
    cursor: datetime | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Ascending raw readings; second value says whether more rows follow."""
    sql = (
        f"SELECT {_READING_COLUMNS} FROM readings_scaled "
        "WHERE device_id = %s AND t >= %s AND t <= %s"
    )
    params: list[Any] = [device_id, t_from, t_to]
    if cursor is not None:
        sql += " AND t > %s"
        params.append(cursor)
    sql += " ORDER BY t ASC LIMIT %s"
    params.append(limit + 1)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = list(cur.fetchall())
    more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        row["temp1_c"] = _temp(row["temp1_c"])
        row["temp2_c"] = _temp(row["temp2_c"])
    return rows, more


def readings_bucketed(
    conn: psycopg.Connection,
    device_id: str,
    bucket: str,
    t_from: datetime,
    t_to: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    """Averages per UTC bucket and sensor period (a calibration change inside a
    bucket yields two rows, never a mix of units)."""
    trunc = _BUCKET_TRUNC[bucket]
    sql = f"""
        SELECT
            date_trunc('{trunc}', t, 'UTC') AS t,
            count(*) AS n,
            avg(idc_ma) AS idc_ma,
            avg(vdc_v) AS vdc_v,
            avg(value) AS value,
            min(value) AS value_min,
            max(value) AS value_max,
            max(unit) AS unit,
            max(CASE quality
                    WHEN 'no_signal' THEN 4
                    WHEN 'fault' THEN 3
                    WHEN 'saturated' THEN 2
                    WHEN 'ok' THEN 1
                    ELSE 0 END) AS quality_rank
        FROM readings_scaled
        WHERE device_id = %s AND t >= %s AND t <= %s
        GROUP BY date_trunc('{trunc}', t, 'UTC'), sensor_id
        ORDER BY 1, sensor_id
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (device_id, t_from, t_to, limit))
        rows = list(cur.fetchall())
    names = {v: k for k, v in QUALITY_RANK.items()}
    for row in rows:
        row["quality"] = names.get(row.pop("quality_rank"))
    return rows


# --- events / uplinks ----------------------------------------------------------------


def list_events(
    conn: psycopg.Connection,
    device_id: str,
    kinds: tuple[str, ...] = EVENT_KINDS,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, received_at, kind, payload, fw_version
            FROM uplinks
            WHERE device_id = %s AND kind = ANY(%s)
            ORDER BY received_at DESC, id DESC
            LIMIT %s
            """,
            (device_id, list(kinds), limit),
        )
        rows = list(cur.fetchall())
    return [map_event(r) for r in rows]


def map_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(row["payload"])
    kind = row["kind"]
    result = version = info = None
    if kind == "dl_ack":
        result = payload.get("Downklink_Ack")
        info = payload.get("Error")
    elif kind == "ota":
        result = payload.get(OTA_RESULT_KEY)
        version = payload.get("Version")
        info = payload.get("Info")
    elif kind == "status":
        version = payload.get("Image Version")
    if version is None and kind in ("ota", "status"):
        version = row.get("fw_version")
    return {
        "id": row["id"],
        "t": row["received_at"],
        "kind": kind,
        "result": None if result is None else str(result),
        "version": None if version is None else str(version),
        "info": None if info is None else str(info),
        "payload": row["payload"] if isinstance(row["payload"], dict) else payload,
    }


def list_uplinks(
    conn: psycopg.Connection, device_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, topic, kind, received_at, payload, battery, "signal" AS signal,
                   fw_version
            FROM uplinks
            WHERE device_id = %s
            ORDER BY received_at DESC, id DESC
            LIMIT %s
            """,
            (device_id, limit),
        )
        rows = list(cur.fetchall())
    for row in rows:
        row["payload"] = row["payload"] if not isinstance(row["payload"], str) else _payload(row["payload"])
        row["payload_pretty"] = json.dumps(row["payload"], indent=2, sort_keys=True)
        row["signal"] = _num(row["signal"])
    return rows


# --- sensors (calibration periods) --------------------------------------------------------


class DuplicateValidFrom(Exception):
    """A sensors row with the same (device_id, valid_from) already exists."""


def list_sensors(conn: psycopg.Connection, device_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {SENSOR_COLUMNS} FROM sensors WHERE device_id = %s "
            "ORDER BY valid_from DESC",
            (device_id,),
        )
        return list(cur.fetchall())


def get_sensor(
    conn: psycopg.Connection, device_id: str, sensor_id: int
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {SENSOR_COLUMNS} FROM sensors WHERE device_id = %s AND id = %s",
            (device_id, sensor_id),
        )
        return cur.fetchone()


def insert_sensor(
    conn: psycopg.Connection, device_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO sensors (device_id, valid_from, channel, kind, unit,
                    in_low, in_high, range_low, range_high, "offset", label)
                VALUES (%(device_id)s, %(valid_from)s, %(channel)s, %(kind)s, %(unit)s,
                    %(in_low)s, %(in_high)s, %(range_low)s, %(range_high)s,
                    %(offset)s, %(label)s)
                RETURNING {SENSOR_COLUMNS}
                """,
                {**data, "device_id": device_id},
            )
            row = cur.fetchone()
        conn.commit()
    except UniqueViolation as exc:
        conn.rollback()
        raise DuplicateValidFrom from exc
    assert row is not None
    return row


def update_sensor(
    conn: psycopg.Connection, device_id: str, sensor_id: int, data: dict[str, Any]
) -> dict[str, Any] | None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE sensors SET valid_from = %(valid_from)s, channel = %(channel)s,
                    kind = %(kind)s, unit = %(unit)s, in_low = %(in_low)s,
                    in_high = %(in_high)s, range_low = %(range_low)s,
                    range_high = %(range_high)s, "offset" = %(offset)s, label = %(label)s
                WHERE device_id = %(device_id)s AND id = %(sensor_id)s
                RETURNING {SENSOR_COLUMNS}
                """,
                {**data, "device_id": device_id, "sensor_id": sensor_id},
            )
            row = cur.fetchone()
        conn.commit()
    except UniqueViolation as exc:
        conn.rollback()
        raise DuplicateValidFrom from exc
    return row


def delete_sensor(conn: psycopg.Connection, device_id: str, sensor_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sensors WHERE device_id = %s AND id = %s", (device_id, sensor_id)
        )
        deleted = cur.rowcount > 0
    conn.commit()
    return deleted
