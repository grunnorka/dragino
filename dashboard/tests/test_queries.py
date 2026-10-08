"""queries.py helpers that need no HTTP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dashboard import queries
from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import API_DB_URL, DEV, add_device, add_reading, add_uplink, utcnow


def test_iso_and_to_utc():
    est = timezone(timedelta(hours=-5))
    t = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=est)
    assert queries.iso(t) == "2026-01-02T08:04:05.123456Z"
    assert queries.iso(datetime(2026, 1, 2)) == "2026-01-02T00:00:00Z"
    assert queries.iso(None) is None


def test_device_status():
    now = utcnow()
    assert queries.device_status(None, 24) == "never-seen"
    assert queries.device_status(now - timedelta(hours=1), 24) == "ok"
    assert queries.device_status(now - timedelta(hours=25), 24) == "stale"


def test_map_event_variants():
    base = {"id": 1, "received_at": utcnow(), "fw_version": None}
    assert queries.map_event({**base, "kind": "dl_ack", "payload": {"Downklink_Ack": "reverted"}})["result"] == "reverted"
    ota = queries.map_event({**base, "kind": "ota", "payload": {"OTA": "failed", "Version": "v", "Info": "i"}})
    assert (ota["result"], ota["version"], ota["info"]) == ("failed", "v", "i")
    st = queries.map_event({**base, "kind": "status", "payload": {}, "fw_version": "fwcol"})
    assert st["version"] == "fwcol"
    assert queries.map_event({**base, "kind": "ota", "payload": "garbage"})["result"] is None


def test_open_conn_per_call_and_closed(api_env, api_conn):
    a = queries.open_conn(API_DB_URL)
    b = queries.open_conn(API_DB_URL)
    assert a is not b
    a.close()
    b.close()
    assert a.closed and b.closed


def test_last_uplink_at_picks_newest_across_devices(api_conn):
    now = utcnow()
    assert queries.last_uplink_at(api_conn) is None
    add_device(api_conn, DEV)
    add_device(api_conn, "ltc2-1")
    add_uplink(api_conn, DEV, received_at=now - timedelta(hours=5))
    add_uplink(api_conn, "ltc2-1", received_at=now - timedelta(hours=1))
    assert abs((queries.last_uplink_at(api_conn) - (now - timedelta(hours=1))).total_seconds()) < 1


def test_list_uplinks_has_kind(api_conn):
    add_device(api_conn, DEV)
    add_uplink(api_conn, DEV, kind="status", payload={"a": 1}, signal=13.0)
    row = queries.list_uplinks(api_conn, DEV)[0]
    assert row["kind"] == "status" and row["signal"] == 13 and '"a": 1' in row["payload_pretty"]
