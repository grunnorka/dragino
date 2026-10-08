"""GET /api/v1/devices/{id}/readings: raw paging, 1h/1d buckets, quality."""
from __future__ import annotations

from datetime import timedelta

import pytest

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import (
    DEV, RW_H, add_device, add_reading, add_sensor, hour_floor, utcnow,
)

URL = f"/api/v1/devices/{DEV}/readings"


@pytest.fixture()
def dev(api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    return api_conn


def test_raw_defaults_ascending_and_shape(client, dev):
    now = utcnow()
    add_sensor(dev, DEV, now - timedelta(days=30), range_high=10)
    add_reading(dev, DEV, now - timedelta(days=8), 6.0)  # outside default 7 d window
    add_reading(dev, DEV, now - timedelta(hours=2), 12.0, source="clocklog")
    add_reading(dev, DEV, now - timedelta(hours=1), 20.0)
    r = client.get(URL, headers=RW_H)
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["device_id", "bucket", "readings", "next_cursor"]
    assert body["bucket"] == "raw" and body["device_id"] == DEV
    assert body["next_cursor"] is None
    rows = body["readings"]
    assert [x["idc_ma"] for x in rows] == [12.0, 20.0]
    assert list(rows[0]) == [
        "t", "source", "idc_ma", "vdc_v", "temp1_c", "temp2_c", "value", "unit", "quality",
    ]
    assert rows[0]["source"] == "clocklog"
    assert rows[0]["value"] == 5.0 and rows[1]["value"] == 10.0
    assert rows[0]["t"].endswith("Z")


def test_raw_cursor_paging(client, dev):
    base = utcnow() - timedelta(hours=5)
    for i in range(25):
        add_reading(dev, DEV, base + timedelta(seconds=i * 7, microseconds=123), 10.0 + i * 0.01)
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        body = client.get(URL, params=params, headers=RW_H).json()
        pages += 1
        seen += [x["t"] for x in body["readings"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert cursor == body["readings"][-1]["t"]
        assert pages < 10
    assert pages == 3
    assert len(seen) == 25 and len(set(seen)) == 25 and seen == sorted(seen)


def test_raw_exact_limit_has_no_cursor(client, dev):
    base = utcnow() - timedelta(hours=1)
    for i in range(3):
        add_reading(dev, DEV, base + timedelta(minutes=i), 10.0)
    body = client.get(URL, params={"limit": 3}, headers=RW_H).json()
    assert len(body["readings"]) == 3 and body["next_cursor"] is None


def test_raw_from_to(client, dev):
    base = hour_floor(utcnow()) - timedelta(days=2)
    for i in range(6):
        add_reading(dev, DEV, base + timedelta(hours=i), 10.0 + i)
    params = {
        "from": (base + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "to": (base + timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
    }
    body = client.get(URL, params=params, headers=RW_H).json()
    assert [x["idc_ma"] for x in body["readings"]] == [11.0, 12.0, 13.0]


def test_uncalibrated_value_is_null(client, dev):
    add_reading(dev, DEV, utcnow() - timedelta(hours=1), 12.0)
    r = client.get(URL, headers=RW_H).json()["readings"][0]
    assert r["value"] is None and r["unit"] is None and r["quality"] == "ok"


@pytest.mark.parametrize(
    "ma,quality",
    [(0.0, "no_signal"), (0.49, "no_signal"), (3.0, "fault"), (3.7, "saturated"),
     (12.0, "ok"), (20.7, "saturated"), (21.5, "fault"), (None, None)],
)
def test_quality_levels(client, dev, ma, quality):
    add_reading(dev, DEV, utcnow() - timedelta(hours=1), ma)
    r = client.get(URL, headers=RW_H).json()["readings"][0]
    assert r["quality"] == quality


def test_bucket_1h_calibration_change_mid_bucket(client, dev):
    h = hour_floor(utcnow()) - timedelta(days=2)
    add_sensor(dev, DEV, h - timedelta(days=1), unit="m", range_low=0, range_high=10)
    add_sensor(dev, DEV, h + timedelta(minutes=30), unit="kPa", range_low=0, range_high=100)
    add_reading(dev, DEV, h + timedelta(minutes=5), 8.0)    # 2.5 m
    add_reading(dev, DEV, h + timedelta(minutes=15), 12.0)  # 5.0 m
    add_reading(dev, DEV, h + timedelta(minutes=35), 8.0)   # 25 kPa
    add_reading(dev, DEV, h + timedelta(minutes=45), 0.0)   # -25 kPa, no_signal
    add_reading(dev, DEV, h + timedelta(hours=1, minutes=5), 20.0)  # next bucket, 100 kPa
    body = client.get(URL, params={"bucket": "1h"}, headers=RW_H).json()
    assert list(body) == ["device_id", "bucket", "readings", "next_cursor"]
    assert body["bucket"] == "1h" and body["next_cursor"] is None
    rows = body["readings"]
    assert len(rows) == 3
    assert list(rows[0]) == [
        "t", "n", "idc_ma", "vdc_v", "value", "value_min", "value_max", "unit", "quality",
    ]
    first, second, third = rows
    assert first["t"] == second["t"] == h.isoformat().replace("+00:00", "Z")
    assert first["unit"] == "m" and second["unit"] == "kPa"  # never mixed
    assert first["n"] == 2 and first["value"] == pytest.approx(3.75)
    assert first["value_min"] == pytest.approx(2.5) and first["value_max"] == pytest.approx(5.0)
    assert first["idc_ma"] == pytest.approx(10.0) and first["quality"] == "ok"
    assert second["n"] == 2 and second["value"] == pytest.approx(0.0)
    assert second["value_min"] == pytest.approx(-25.0) and second["value_max"] == pytest.approx(25.0)
    assert second["quality"] == "no_signal"  # worst in bucket
    assert third["t"] == (h + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert third["value"] == pytest.approx(100.0) and third["quality"] == "ok"


def test_bucket_worst_quality_order(client, dev):
    h = hour_floor(utcnow()) - timedelta(days=1)
    for minute, ma in ((1, 12.0), (2, 3.7), (3, 3.0)):  # ok, saturated, fault
        add_reading(dev, DEV, h + timedelta(minutes=minute), ma)
    rows = client.get(URL, params={"bucket": "1h"}, headers=RW_H).json()["readings"]
    assert rows[0]["quality"] == "fault"
    # saturated beats ok
    h2 = h + timedelta(hours=3)
    for minute, ma in ((1, 12.0), (2, 3.7)):
        add_reading(dev, DEV, h2 + timedelta(minutes=minute), ma)
    rows = client.get(URL, params={"bucket": "1h"}, headers=RW_H).json()["readings"]
    assert [r["quality"] for r in rows] == ["fault", "saturated"]


def test_bucket_1d_uncalibrated(client, dev):
    d = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    add_reading(dev, DEV, d + timedelta(hours=1), 4.0)
    add_reading(dev, DEV, d + timedelta(hours=23), 8.0)
    add_reading(dev, DEV, d + timedelta(days=1, hours=1), 20.0)
    rows = client.get(URL, params={"bucket": "1d"}, headers=RW_H).json()["readings"]
    assert [r["t"] for r in rows] == [
        d.isoformat().replace("+00:00", "Z"),
        (d + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
    ]
    assert rows[0]["n"] == 2 and rows[0]["idc_ma"] == pytest.approx(6.0)
    assert rows[0]["value"] is None and rows[0]["unit"] is None


def test_validation_and_404(client, dev):
    assert client.get(URL, params={"bucket": "5m"}, headers=RW_H).status_code == 422
    assert client.get(URL, params={"limit": 0}, headers=RW_H).status_code == 422
    assert client.get(URL, params={"limit": 20001}, headers=RW_H).status_code == 422
    assert client.get(URL, params={"limit": 20000}, headers=RW_H).status_code == 200
    assert client.get(URL, params={"from": "garbage"}, headers=RW_H).status_code == 422
    r = client.get(URL, params={"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, headers=RW_H)
    assert r.status_code == 422
    assert client.get("/api/v1/devices/nope/readings", headers=RW_H).status_code == 404
