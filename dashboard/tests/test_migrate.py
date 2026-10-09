"""Backfill: re-homing, kind/identity/readings backfill, idempotency, dry run."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from dashboard.db import SCHEMA_SQL
from dashboard.migrate import main, migrate
from dashboard.tests.conftest import IMEI, NOW, uplink_payload

REAL = f"ps-cb-{IMEI}"
OTHER_IMEI = "869181074157262"


def rows(conn: psycopg.Connection, sql: str, *args: object) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        out = list(cur.fetchall()) if cur.description else []
    conn.commit()
    return out


def add_uplink(conn: psycopg.Connection, device: str, payload: dict, at: datetime) -> None:
    """Insert the way the OLD ingest did: no kind/fw_version, imei/model from the payload."""
    rows(
        conn,
        "INSERT INTO uplinks (device_id, topic, received_at, payload, imei, model) "
        "VALUES (%s, 'dragino/ps-cb/up', %s, %s::jsonb, %s, %s)",
        device, at, json.dumps(payload), payload.get("IMEI"), payload.get("Model"),
    )


@pytest.fixture()
def legacy(conn: psycopg.Connection) -> psycopg.Connection:
    """Production-like state: real device, bare-IMEI twin, a second clean device."""
    # devices of the old schema carry no imei/model yet
    for dev in (REAL, IMEI, f"ps-cb-{OTHER_IMEI}"):
        rows(conn, "INSERT INTO devices (id, first_seen_at, last_seen_at, created_via) "
                   "VALUES (%s, %s, %s, 'auto')", dev, NOW, NOW + timedelta(hours=3))
    add_uplink(conn, REAL, uplink_payload(), NOW)
    add_uplink(conn, REAL, uplink_payload(
        time="2026-10-08T12:31:02Z",
        **{"1": [3.97, 0.0, "2026-10-08T10:31:00Z"], "2": [3.961, 0.0, "2026-10-08T08:31:00Z"]}),
        NOW + timedelta(hours=2))
    add_uplink(conn, IMEI, {"IMEI": IMEI, "Downklink_Ack": "success"}, NOW + timedelta(minutes=1))
    add_uplink(conn, IMEI, {"IMEI": IMEI, "OTA": "applied", "Version": "openfw-0.3.2"},
               NOW + timedelta(minutes=9))
    add_uplink(conn, f"ps-cb-{OTHER_IMEI}", uplink_payload(IMEI=OTHER_IMEI), NOW)
    return conn


def snapshot(conn: psycopg.Connection) -> dict:
    return {
        "devices": rows(conn, "SELECT id, imei, model, first_seen_at, last_seen_at FROM devices ORDER BY id"),
        "uplinks": rows(conn, "SELECT id, device_id, kind, fw_version FROM uplinks ORDER BY id"),
        "readings": rows(conn, "SELECT device_id, t, source, idc_ma, uplink_id FROM readings ORDER BY device_id, t"),
    }


def test_migrate_end_to_end_and_idempotent(legacy: psycopg.Connection) -> None:
    conn = legacy
    summary = migrate(conn)
    conn.commit()

    assert summary["bare_devices_found"] == 1
    assert summary["bare_devices_rehomed"] == 1
    assert summary["rehomed_uplinks"] == 2
    assert summary["uplinks_kind_updated"] == 2  # the ack and the OTA were stored as 'uplink'
    assert summary["readings_inserted"] == 3 + 2 + 3  # REAL: 3 + 2 new; other device: 3

    snap = snapshot(conn)
    assert [d["id"] for d in snap["devices"]] == [f"ps-cb-{OTHER_IMEI}", REAL]
    real = next(d for d in snap["devices"] if d["id"] == REAL)
    assert (real["imei"], real["model"]) == (IMEI, "ps-cb")
    assert real["last_seen_at"] == NOW + timedelta(hours=3)
    assert [(u["device_id"], u["kind"], u["fw_version"]) for u in snap["uplinks"]] == [
        (REAL, "uplink", None), (REAL, "uplink", None),
        (REAL, "dl_ack", None), (REAL, "ota", "openfw-0.3.2"),
        (f"ps-cb-{OTHER_IMEI}", "uplink", None),
    ]
    assert len([r for r in snap["readings"] if r["device_id"] == REAL]) == 5

    again = migrate(conn)
    conn.commit()
    assert again["bare_devices_found"] == 0
    for key in ("uplinks_kind_updated", "devices_identity_filled", "bare_devices_rehomed",
                "rehomed_uplinks", "rehomed_readings", "readings_inserted"):
        assert again[key] == 0, key
    assert snapshot(conn) == snap


def test_bare_device_without_target_is_kept(conn: psycopg.Connection) -> None:
    rows(conn, "INSERT INTO devices (id, created_via) VALUES (%s, 'auto')", IMEI)
    add_uplink(conn, IMEI, {"IMEI": IMEI, "Downklink_Ack": "success"}, NOW)
    s = migrate(conn)
    conn.commit()
    assert (s["bare_devices_found"], s["bare_devices_kept"], s["bare_devices_rehomed"]) == (1, 1, 0)
    (dev,) = rows(conn, "SELECT id, imei, model FROM devices")
    assert (dev["id"], dev["imei"], dev["model"]) == (IMEI, IMEI, None)


def test_bare_device_with_blocking_sensor_row_is_kept(conn: psycopg.Connection) -> None:
    rows(conn, "INSERT INTO devices (id, created_via) VALUES (%s, 'auto'), (%s, 'auto')", REAL, IMEI)
    for dev in (REAL, IMEI):  # same valid_from on both: the bare one cannot move
        rows(conn, "INSERT INTO sensors (device_id, valid_from, unit, range_low, range_high) "
                   "VALUES (%s, %s, 'm', 0, 10)", dev, NOW)
    s = migrate(conn)
    conn.commit()
    assert (s["bare_devices_rehomed"], s["bare_devices_kept"]) == (0, 1)
    assert len(rows(conn, "SELECT 1 FROM devices")) == 2


def test_bare_sensors_and_readings_move(conn: psycopg.Connection) -> None:
    rows(conn, "INSERT INTO devices (id, created_via) VALUES (%s, 'auto'), (%s, 'auto')", REAL, IMEI)
    rows(conn, "INSERT INTO sensors (device_id, valid_from, unit, range_low, range_high) "
               "VALUES (%s, %s, 'm', 0, 10)", IMEI, NOW)
    for dev, v in ((REAL, 1.0), (IMEI, 2.0)):  # same t: the target's row wins
        rows(conn, "INSERT INTO readings (device_id, t, source, idc_ma) VALUES (%s, %s, 'uplink', %s)",
             dev, NOW, v)
    rows(conn, "INSERT INTO readings (device_id, t, source, idc_ma) VALUES (%s, %s, 'uplink', 9)",
         IMEI, NOW + timedelta(hours=1))
    migrate(conn)
    conn.commit()
    assert [d["id"] for d in rows(conn, "SELECT id FROM devices")] == [REAL]
    assert len(rows(conn, "SELECT 1 FROM sensors WHERE device_id = %s", REAL)) == 1
    got = rows(conn, "SELECT t, idc_ma FROM readings WHERE device_id = %s ORDER BY t", REAL)
    assert [r["idc_ma"] for r in got] == [1.0, 9.0]


def test_dry_run_rolls_back(legacy: psycopg.Connection, db_url: str, monkeypatch: pytest.MonkeyPatch,
                            capsys: pytest.CaptureFixture[str]) -> None:
    before = snapshot(legacy)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DEVICE_IDS", "")
    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "rehomed_uplinks" in out
    assert "dragino:dragino" not in out and db_url not in out
    assert snapshot(legacy) == before

    assert main([]) == 0
    assert "committed" in capsys.readouterr().out
    assert snapshot(legacy) != before


def test_schema_only_database(db_url: str) -> None:
    """Fresh DB (no tables at all): migrate creates the schema and is a no-op."""
    with psycopg.connect(db_url) as c:
        c.execute("DROP VIEW IF EXISTS readings_scaled")
        c.execute("DROP TABLE IF EXISTS readings, sensors, uplinks, devices CASCADE")
        summary = migrate(c)  # type: ignore[arg-type]
        c.commit()
    assert summary["readings_inserted"] == 0 and summary["bare_devices_found"] == 0


def test_old_schema_gets_upgraded(db_url: str) -> None:
    """Production state: devices/uplinks from before API.md, no new columns."""
    old_schema = SCHEMA_SQL.split("-- v1 telemetry")[0]
    with psycopg.connect(db_url) as c:
        c.execute("DROP VIEW IF EXISTS readings_scaled")
        c.execute("DROP TABLE IF EXISTS readings, sensors, uplinks, devices CASCADE")
        c.execute(old_schema)
        c.execute("INSERT INTO devices (id, created_via) VALUES (%s, 'auto')", (REAL,))
        c.execute(
            "INSERT INTO uplinks (device_id, topic, payload, imei, model) "
            "VALUES (%s, 't', %s::jsonb, %s, 'PS-CB')", (REAL, json.dumps(uplink_payload()), IMEI))
        c.commit()
        from dashboard.db import connect
        with connect(db_url) as c2:
            migrate(c2)
            c2.commit()
            (r,) = list(c2.execute("SELECT count(*) AS n FROM readings"))
            assert r["n"] == 3
            (d,) = list(c2.execute("SELECT imei, model FROM devices"))
            assert (d["imei"], d["model"]) == (IMEI, "ps-cb")
