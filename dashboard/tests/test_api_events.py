"""GET /api/v1/devices/{id}/events mapping per kind."""
from __future__ import annotations

from datetime import timedelta

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import DEV, RW_H, add_device, add_uplink, utcnow

URL = f"/api/v1/devices/{DEV}/events"


def seed(c):
    now = utcnow()
    add_device(c, DEV, last_seen=now)
    add_uplink(c, DEV, kind="uplink", received_at=now - timedelta(minutes=50), payload={"idc_input": 4.0})
    add_uplink(c, DEV, kind="ota", received_at=now - timedelta(minutes=40), fw_version="openfw-0.3.2",
               payload={"IMEI": "1", "OTA": "applied", "Version": "openfw-0.3.2", "Info": "ok"})
    add_uplink(c, DEV, kind="dl_ack", received_at=now - timedelta(minutes=30),
               payload={"IMEI": "1", "Downklink_Ack": "error", "Error": "bad value"})
    add_uplink(c, DEV, kind="status", received_at=now - timedelta(minutes=20),
               payload={"IMEI": "1", "Image Version": "openfw-0.3.1", "Model": "PS-CB"})
    add_uplink(c, DEV, kind="other", received_at=now - timedelta(minutes=10), payload={"raw": 1})


def test_events_newest_first_and_mapping(client, api_conn):
    seed(api_conn)
    body = client.get(URL, headers=RW_H).json()
    assert list(body) == ["events"]
    ev = body["events"]
    assert [e["kind"] for e in ev] == ["status", "dl_ack", "ota"]  # uplink/other excluded
    assert all(list(e) == ["id", "t", "kind", "result", "version", "info", "payload"] for e in ev)
    status, ack, ota = ev
    assert (status["result"], status["version"], status["info"]) == (None, "openfw-0.3.1", None)
    assert status["payload"]["Model"] == "PS-CB"
    assert (ack["result"], ack["version"], ack["info"]) == ("error", None, "bad value")
    assert (ota["result"], ota["version"], ota["info"]) == ("applied", "openfw-0.3.2", "ok")
    assert ota["t"].endswith("Z")


def test_events_kind_filter_limit_and_errors(client, api_conn):
    seed(api_conn)
    now = utcnow()
    add_uplink(api_conn, DEV, kind="ota", received_at=now, payload={"OTA": "downloaded", "Version": "x"})
    only = client.get(URL, params={"kind": "ota"}, headers=RW_H).json()["events"]
    assert [e["result"] for e in only] == ["downloaded", "applied"]
    assert len(client.get(URL, params={"limit": 2}, headers=RW_H).json()["events"]) == 2
    assert client.get(URL, params={"kind": "uplink"}, headers=RW_H).status_code == 422
    assert client.get(URL, params={"limit": 0}, headers=RW_H).status_code == 422
    assert client.get("/api/v1/devices/nope/events", headers=RW_H).status_code == 404


def test_events_empty(client, api_conn):
    add_device(api_conn, DEV)
    assert client.get(URL, headers=RW_H).json() == {"events": []}
