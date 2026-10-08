"""GET/PATCH /api/v1/devices: exact key sets from API.md."""
from __future__ import annotations

from datetime import timedelta

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import (
    DEV, DEV2, RW_H, add_device, add_reading, add_sensor, add_uplink, utcnow,
)

DEVICE_KEYS = [
    "id", "label", "model", "imei", "first_seen_at", "last_seen_at", "status",
    "battery_v", "signal_csq", "fw_version", "last_ota", "sensor", "latest",
]
SENSOR_KEYS = [
    "id", "valid_from", "channel", "kind", "unit", "in_low", "in_high",
    "range_low", "range_high", "offset", "label",
]
READING_KEYS = [
    "t", "source", "idc_ma", "vdc_v", "temp1_c", "temp2_c", "value", "unit", "quality",
]


def seed_full(c):
    now = utcnow()
    add_device(
        c, DEV, label="Bench unit", model="ps-cb", imei="869181074164029",
        first_seen=now - timedelta(days=3), last_seen=now - timedelta(minutes=5),
    )
    add_uplink(c, DEV, received_at=now - timedelta(hours=2), battery=3.4, signal=9, fw_version="openfw-0.3.1")
    add_uplink(c, DEV, received_at=now - timedelta(minutes=5), battery=3.512, signal=13, fw_version="openfw-0.3.2")
    # newer non-uplink rows must not leak into battery/signal/fw
    add_uplink(
        c, DEV, kind="ota", received_at=now - timedelta(minutes=1), battery=1.0, signal=1,
        payload={"IMEI": "1", "OTA": "applied", "Version": "openfw-0.3.2"},
    )
    add_sensor(c, DEV, now - timedelta(days=2), unit="m", range_low=0, range_high=10, label="well")
    add_reading(c, DEV, now - timedelta(hours=1), 8.0)
    add_reading(c, DEV, now - timedelta(minutes=10), 12.0, temp1_c=20.5)


def test_device_list_exact_shape(client, api_conn):
    seed_full(api_conn)
    r = client.get("/api/v1/devices", headers=RW_H)
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["devices"]
    d = body["devices"][0]
    assert list(d) == DEVICE_KEYS
    assert list(d["sensor"]) == SENSOR_KEYS
    assert list(d["latest"]) == READING_KEYS
    assert list(d["last_ota"]) == ["t", "result", "version", "info"]

    assert d["id"] == DEV and d["label"] == "Bench unit"
    assert d["model"] == "ps-cb" and d["imei"] == "869181074164029"
    assert d["status"] == "ok"
    assert d["battery_v"] == 3.512
    assert d["signal_csq"] == 13 and isinstance(d["signal_csq"], int)
    assert d["fw_version"] == "openfw-0.3.2"
    assert d["last_ota"]["result"] == "applied"
    assert d["last_ota"]["version"] == "openfw-0.3.2"
    assert d["last_ota"]["info"] is None
    assert d["sensor"]["unit"] == "m" and d["sensor"]["label"] == "well"
    assert d["latest"]["idc_ma"] == 12.0
    assert d["latest"]["value"] == 5.0 and d["latest"]["unit"] == "m"
    assert d["latest"]["quality"] == "ok" and d["latest"]["temp1_c"] == 20.5
    assert d["latest"]["source"] == "uplink"


def test_timestamps_are_utc_z(client, api_conn):
    seed_full(api_conn)
    d = client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()
    for ts in (d["first_seen_at"], d["last_seen_at"], d["latest"]["t"],
               d["sensor"]["valid_from"], d["last_ota"]["t"]):
        assert ts.endswith("Z") and "+" not in ts


def test_status_values_and_empty_nulls(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now - timedelta(hours=30))
    add_device(api_conn, DEV2)  # seeded placeholder, never seen
    devs = {d["id"]: d for d in client.get("/api/v1/devices", headers=RW_H).json()["devices"]}
    assert devs[DEV]["status"] == "stale"
    never = devs[DEV2]
    assert never["status"] == "never-seen"
    assert list(never) == DEVICE_KEYS
    for key in DEVICE_KEYS[1:]:
        if key != "status":
            assert never[key] is None, key


def test_device_one_and_404(client, api_conn):
    seed_full(api_conn)
    r = client.get(f"/api/v1/devices/{DEV}", headers=RW_H)
    assert r.status_code == 200 and list(r.json()) == DEVICE_KEYS
    r = client.get("/api/v1/devices/ps-cb-000", headers=RW_H)
    assert r.status_code == 404 and r.json() == {"detail": "Unknown device"}


def test_future_calibration_is_not_current(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now)
    add_sensor(api_conn, DEV, now + timedelta(days=1))
    assert client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()["sensor"] is None


def test_patch_label_set_and_clear(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    r = client.patch(f"/api/v1/devices/{DEV}", json={"label": "  Pump 3 "}, headers=RW_H)
    assert r.status_code == 200 and r.json()["label"] == "Pump 3"
    r = client.patch(f"/api/v1/devices/{DEV}", json={"label": None}, headers=RW_H)
    assert r.status_code == 200 and r.json()["label"] is None
    r = client.patch(f"/api/v1/devices/{DEV}", json={"label": "  "}, headers=RW_H)
    assert r.json()["label"] is None
    assert client.patch(f"/api/v1/devices/{DEV}", json={}, headers=RW_H).status_code == 422
    assert client.patch("/api/v1/devices/nope", json={"label": "x"}, headers=RW_H).status_code == 404


def test_fw_version_ignores_failed_ota_target(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now)
    add_uplink(api_conn, DEV, received_at=now - timedelta(hours=3), fw_version="openfw-0.3.1")
    # failed / rolled back OTA: its Version is only the target
    add_uplink(api_conn, DEV, kind="ota", received_at=now - timedelta(hours=2), fw_version="openfw-0.4.0",
               payload={"OTA": "failed", "Version": "openfw-0.4.0", "Info": "crc"})
    add_uplink(api_conn, DEV, kind="ota", received_at=now - timedelta(hours=1), fw_version="openfw-0.4.0",
               payload={"OTA": "rolled back", "Version": "openfw-0.4.0"})
    d = client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()
    assert d["fw_version"] == "openfw-0.3.1"
    assert d["last_ota"]["result"] == "rolled back"  # newest ota row regardless of result
    assert d["last_ota"]["version"] == "openfw-0.4.0"


def test_fw_version_takes_applied_ota_and_status(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now)
    add_uplink(api_conn, DEV, received_at=now - timedelta(hours=3), fw_version="openfw-0.3.1")
    add_uplink(api_conn, DEV, kind="ota", received_at=now - timedelta(hours=2), fw_version="openfw-0.4.0",
               payload={"OTA": "applied", "Version": "openfw-0.4.0"})
    d = client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()
    assert d["fw_version"] == "openfw-0.4.0"
    add_uplink(api_conn, DEV, kind="ota", received_at=now - timedelta(minutes=50), fw_version="openfw-0.5.0",
               payload={"OTA": "restored", "Version": "openfw-0.5.0"})
    assert client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()["fw_version"] == "openfw-0.5.0"
    add_uplink(api_conn, DEV, kind="status", received_at=now - timedelta(minutes=10), fw_version="openfw-0.3.2",
               payload={"Image Version": "openfw-0.3.2"})
    assert client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()["fw_version"] == "openfw-0.3.2"


def test_temperature_sentinels_are_null(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now)
    add_reading(api_conn, DEV, now - timedelta(minutes=3), 12.0, temp1_c=-327.6)
    add_reading(api_conn, DEV, now - timedelta(minutes=2), 12.0, temp1_c=21.5)
    add_reading(api_conn, DEV, now - timedelta(minutes=1), 12.0, temp1_c=-983.0)
    rows = client.get(f"/api/v1/devices/{DEV}/readings", headers=RW_H).json()["readings"]
    assert [r["temp1_c"] for r in rows] == [None, 21.5, None]
    latest = client.get(f"/api/v1/devices/{DEV}", headers=RW_H).json()["latest"]
    assert latest["temp1_c"] is None
    with api_conn.cursor() as cur:  # raw stays in the DB
        cur.execute("SELECT temp1_c FROM readings ORDER BY t")
        assert [r["temp1_c"] for r in cur.fetchall()] == [-327.6, 21.5, -983.0]
