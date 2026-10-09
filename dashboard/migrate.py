"""Idempotent backfill for the production database.

Run: python -m dashboard.migrate [--dry-run]

Steps (all in one transaction; ``--dry-run`` rolls it back):

1. ``ensure_schema`` (API.md §1 tables/columns/view)
2. ``uplinks.kind`` / ``uplinks.fw_version`` recomputed from each payload
3. ``devices.imei`` / ``devices.model`` filled (never overwritten) from the
   newest uplink, falling back to the ``{slug}-{IMEI}`` device id
4. bare-IMEI devices folded into the ``{slug}-{IMEI}`` device (uplinks,
   readings, sensors moved; the bare row is deleted only when empty)
5. ``readings`` backfilled from every ``kind='uplink'`` row, same rules as
   live ingest (``ON CONFLICT (device_id, t) DO NOTHING``)

Running it twice changes nothing the second time. The database URL is read from
``DATABASE_URL`` and is never printed.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from typing import Any

import psycopg

from dashboard.db import connect, ensure_schema, insert_readings, rehome_device
from dashboard.extract import (
    classify_kind,
    extract_fw_version,
    extract_readings,
    model_slug,
)
from dashboard.settings import load_settings

log = logging.getLogger("dashboard.migrate")

BATCH = 1000
_BARE_IMEI = re.compile(r"^\d+$")
_MODEL_IMEI_ID = re.compile(r"^(?P<model>.+)-(?P<imei>\d{10,})$")


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _uplink_batches(conn: psycopg.Connection, where: str = "TRUE"):
    """Yield lists of uplink rows in id order (keyset pagination)."""
    last_id = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, device_id, received_at, payload, kind, fw_version
                FROM uplinks WHERE id > %s AND {where}
                ORDER BY id LIMIT %s
                """,  # noqa: S608 - `where` is a constant from this module
                (last_id, BATCH),
            )
            rows = list(cur.fetchall())
        if not rows:
            return
        yield rows
        last_id = rows[-1]["id"]


def backfill_uplink_kinds(conn: psycopg.Connection) -> dict[str, int]:
    scanned = changed = 0
    for rows in _uplink_batches(conn):
        with conn.cursor() as cur:
            for row in rows:
                scanned += 1
                payload = _payload_dict(row["payload"])
                kind = classify_kind(payload)
                fw = extract_fw_version(payload)
                if kind != row["kind"] or fw != row["fw_version"]:
                    cur.execute(
                        "UPDATE uplinks SET kind = %s, fw_version = %s WHERE id = %s",
                        (kind, fw, row["id"]),
                    )
                    changed += 1
    return {"uplinks_scanned": scanned, "uplinks_kind_updated": changed}


def fill_device_identity(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.imei, d.model,
                   (SELECT u.imei FROM uplinks u
                     WHERE u.device_id = d.id AND u.imei IS NOT NULL
                     ORDER BY u.received_at DESC LIMIT 1) AS u_imei,
                   (SELECT u.model FROM uplinks u
                     WHERE u.device_id = d.id AND u.model IS NOT NULL
                     ORDER BY u.received_at DESC LIMIT 1) AS u_model
            FROM devices d
            WHERE d.imei IS NULL OR d.model IS NULL
            """
        )
        rows = list(cur.fetchall())
    updated = 0
    with conn.cursor() as cur:
        for row in rows:
            imei, model = row["imei"], row["model"]
            new_imei = imei or row["u_imei"]
            new_model = model or model_slug(row["u_model"])
            match = _MODEL_IMEI_ID.match(row["id"])
            if match:
                new_imei = new_imei or match["imei"]
                new_model = new_model or model_slug(match["model"])
            elif _BARE_IMEI.match(row["id"]):
                new_imei = new_imei or row["id"]
            if new_imei != imei or new_model != model:
                cur.execute(
                    "UPDATE devices SET imei = %s, model = %s WHERE id = %s",
                    (new_imei, new_model, row["id"]),
                )
                updated += 1
    return {"devices_identity_filled": updated}


def rehome_bare_imei_devices(conn: psycopg.Connection) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM devices WHERE id ~ '^[0-9]+$' ORDER BY id")
        bare_ids = [r["id"] for r in cur.fetchall()]
    totals = {
        "bare_devices_found": len(bare_ids),
        "bare_devices_rehomed": 0,
        "bare_devices_kept": 0,
        "rehomed_uplinks": 0,
        "rehomed_readings": 0,
        "rehomed_sensors": 0,
    }
    for bare in bare_ids:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM devices
                WHERE id <> %(bare)s::text
                  AND right(id, length(%(bare)s::text) + 1) = '-' || %(bare)s::text
                ORDER BY last_seen_at DESC NULLS LAST, id
                """,
                {"bare": bare},
            )
            targets = [r["id"] for r in cur.fetchall()]
            if not targets:
                totals["bare_devices_kept"] += 1
                continue
            if len(targets) > 1:
                log.warning("bare %s matches %d devices; using %s", bare, len(targets), targets[0])
            counts = rehome_device(cur, bare, targets[0])
        totals["rehomed_uplinks"] += counts["uplinks"]
        totals["rehomed_readings"] += counts["readings"]
        totals["rehomed_sensors"] += counts["sensors"]
        if counts["deleted"]:
            totals["bare_devices_rehomed"] += 1
        else:
            totals["bare_devices_kept"] += 1
            log.warning("bare device %s still referenced; row kept", bare)
    return totals


def backfill_readings(conn: psycopg.Connection) -> dict[str, int]:
    scanned = inserted = 0
    for rows in _uplink_batches(conn, "kind = 'uplink'"):
        for row in rows:
            scanned += 1
            readings = extract_readings(_payload_dict(row["payload"]), row["received_at"])
            inserted += insert_readings(conn, row["device_id"], row["id"], readings)
    return {"readings_uplinks_scanned": scanned, "readings_inserted": inserted}


def migrate(conn: psycopg.Connection, device_ids: tuple[str, ...] = ()) -> dict[str, int]:
    """Run every step on ``conn`` without committing; returns the counts."""
    ensure_schema(conn, device_ids, commit=False)
    summary: dict[str, int] = {}
    summary.update(backfill_uplink_kinds(conn))
    identity = fill_device_identity(conn)
    summary.update(rehome_bare_imei_devices(conn))
    # re-homing merges identity into the target; a second pass catches the rest
    identity["devices_identity_filled"] += fill_device_identity(conn)["devices_identity_filled"]
    summary.update(identity)
    summary.update(backfill_readings(conn))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dashboard.migrate", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="do everything, then roll back")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    settings = load_settings()
    try:
        conn = connect(settings.database_url)
    except psycopg.Error as exc:
        print(f"cannot connect to the database: {type(exc).__name__}", file=sys.stderr)
        return 1
    try:
        summary = migrate(conn, settings.device_ids)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

    width = max(len(k) for k in summary)
    print("dry run (rolled back)" if args.dry_run else "migration committed")
    for key, value in summary.items():
        print(f"  {key:<{width}}  {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
