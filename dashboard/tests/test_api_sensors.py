"""Sensors (calibration periods) CRUD, 404/409/422."""
from __future__ import annotations

from datetime import timedelta

import pytest

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import (
    DEV, DEV2, RW_H, add_device, add_reading, add_sensor, utcnow,
)

BASE = f"/api/v1/devices/{DEV}/sensors"
BODY = {"unit": "m", "range_low": 0, "range_high": 10}
KEYS = ["id", "valid_from", "channel", "kind", "unit", "in_low", "in_high",
        "range_low", "range_high", "offset", "label"]


@pytest.fixture()
def dev(api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    return api_conn


def test_create_defaults(client, dev):
    r = client.post(BASE, json=BODY, headers=RW_H)
    assert r.status_code == 201
    s = r.json()
    assert list(s) == KEYS
    assert (s["channel"], s["kind"], s["in_low"], s["in_high"], s["offset"], s["label"]) == (
        "idc", "level", 4, 20, 0, None)
    assert s["valid_from"].endswith("Z") and s["id"] >= 1


def test_create_with_explicit_fields_and_naive_time_is_utc(client, dev):
    body = {**BODY, "valid_from": "2026-01-02T03:04:05", "channel": "vdc", "kind": "pressure",
            "unit": "bar", "in_low": 0, "in_high": 30, "range_high": 6, "offset": -0.5, "label": " tank "}
    s = client.post(BASE, json=body, headers=RW_H).json()
    assert s["valid_from"] == "2026-01-02T03:04:05Z"
    assert (s["channel"], s["kind"], s["unit"], s["in_high"], s["offset"], s["label"]) == (
        "vdc", "pressure", "bar", 30, -0.5, "tank")


def test_list_newest_first(client, dev):
    now = utcnow()
    add_sensor(dev, DEV, now - timedelta(days=5), unit="m")
    add_sensor(dev, DEV, now - timedelta(days=1), unit="kPa")
    add_sensor(dev, DEV, now - timedelta(days=9), unit="bar")
    body = client.get(BASE, headers=RW_H).json()
    assert list(body) == ["sensors"]
    assert [s["unit"] for s in body["sensors"]] == ["kPa", "m", "bar"]
    assert list(body["sensors"][0]) == KEYS


def test_duplicate_valid_from_409(client, dev):
    body = {**BODY, "valid_from": "2026-01-01T00:00:00Z"}
    assert client.post(BASE, json=body, headers=RW_H).status_code == 201
    r = client.post(BASE, json=body, headers=RW_H)
    assert r.status_code == 409 and "detail" in r.json()
    # same instant written with another offset is the same valid_from
    r = client.post(BASE, json={**BODY, "valid_from": "2026-01-01T02:00:00+02:00"}, headers=RW_H)
    assert r.status_code == 409
    # the same valid_from on another device is fine
    add_device(dev, DEV2)
    assert client.post(f"/api/v1/devices/{DEV2}/sensors", json=body, headers=RW_H).status_code == 201


@pytest.mark.parametrize(
    "patch",
    [
        {"in_high": 4},                      # == in_low
        {"in_low": 20},                      # == in_high
        {"channel": "temp"},
        {"unit": ""},
        {"unit": "   "},
        {"unit": None},
        {"range_low": None},
        {"range_high": "abc"},
        {"kind": ""},
        {"valid_from": "yesterday"},
    ],
)
def test_validation_422(client, dev, patch):
    body = {**BODY, **patch}
    body = {k: v for k, v in body.items() if v is not None or k in patch}
    r = client.post(BASE, json=body, headers=RW_H)
    assert r.status_code == 422, patch
    assert client.get(BASE, headers=RW_H).json() == {"sensors": []}


def test_missing_required_fields_422(client, dev):
    assert client.post(BASE, json={"unit": "m"}, headers=RW_H).status_code == 422
    assert client.post(BASE, json={}, headers=RW_H).status_code == 422


def test_unknown_device_404(client, dev):
    assert client.post("/api/v1/devices/nope/sensors", json=BODY, headers=RW_H).status_code == 404
    assert client.get("/api/v1/devices/nope/sensors", headers=RW_H).status_code == 404
    assert client.put("/api/v1/devices/nope/sensors/1", json=BODY, headers=RW_H).status_code == 404
    assert client.delete("/api/v1/devices/nope/sensors/1", headers=RW_H).status_code == 404


def test_put_replaces_and_keeps_valid_from_when_omitted(client, dev):
    created = client.post(BASE, json={**BODY, "valid_from": "2026-01-01T00:00:00Z", "label": "a"},
                          headers=RW_H).json()
    r = client.put(f"{BASE}/{created['id']}", json={"unit": "kPa", "range_low": 0, "range_high": 100},
                   headers=RW_H)
    assert r.status_code == 200
    s = r.json()
    assert s["id"] == created["id"] and s["valid_from"] == "2026-01-01T00:00:00Z"
    assert s["unit"] == "kPa" and s["range_high"] == 100 and s["label"] is None


def test_put_conflict_and_validation_and_404(client, dev):
    a = client.post(BASE, json={**BODY, "valid_from": "2026-01-01T00:00:00Z"}, headers=RW_H).json()
    b = client.post(BASE, json={**BODY, "valid_from": "2026-02-01T00:00:00Z"}, headers=RW_H).json()
    r = client.put(f"{BASE}/{b['id']}", json={**BODY, "valid_from": "2026-01-01T00:00:00Z"}, headers=RW_H)
    assert r.status_code == 409
    assert client.put(f"{BASE}/{a['id']}", json={**BODY, "in_low": 20}, headers=RW_H).status_code == 422
    assert client.put(f"{BASE}/99999", json=BODY, headers=RW_H).status_code == 404
    # a sensor of another device is not reachable through this device
    add_device(dev, DEV2)
    other = client.post(f"/api/v1/devices/{DEV2}/sensors", json=BODY, headers=RW_H).json()
    assert client.put(f"{BASE}/{other['id']}", json=BODY, headers=RW_H).status_code == 404
    assert client.delete(f"{BASE}/{other['id']}", headers=RW_H).status_code == 404


def test_delete(client, dev):
    s = client.post(BASE, json=BODY, headers=RW_H).json()
    r = client.delete(f"{BASE}/{s['id']}", headers=RW_H)
    assert r.status_code == 204 and r.content == b""
    assert client.delete(f"{BASE}/{s['id']}", headers=RW_H).status_code == 404
    assert client.get(BASE, headers=RW_H).json() == {"sensors": []}


def test_calibration_change_recomputes_history(client, dev):
    t = utcnow() - timedelta(hours=1)
    add_reading(dev, DEV, t, 12.0)
    url = f"/api/v1/devices/{DEV}/readings"
    assert client.get(url, headers=RW_H).json()["readings"][0]["value"] is None
    s = client.post(BASE, json={**BODY, "valid_from": (t - timedelta(days=1)).isoformat()},
                    headers=RW_H).json()
    assert client.get(url, headers=RW_H).json()["readings"][0]["value"] == 5.0
    client.put(f"{BASE}/{s['id']}", json={**BODY, "range_high": 20}, headers=RW_H)
    assert client.get(url, headers=RW_H).json()["readings"][0]["value"] == 10.0
