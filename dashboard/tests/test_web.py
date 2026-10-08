"""HTML pages: Basic Auth, rendering, forms, CSRF guard."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import pytest

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import (
    BASIC, DEV, DEV2, add_device, add_reading, add_sensor, add_uplink, utcnow,
)

OWN = {"Origin": "http://testserver"}


def seed(c):
    now = utcnow()
    add_device(c, DEV, label="Bench unit", model="ps-cb", imei="869181074164029",
               first_seen=now - timedelta(days=3), last_seen=now - timedelta(minutes=5))
    add_uplink(c, DEV, received_at=now - timedelta(minutes=5), battery=3.512, signal=13,
               fw_version="openfw-0.3.2", payload={"idc_input": 12.0, "time": "x"})
    add_uplink(c, DEV, kind="ota", received_at=now - timedelta(minutes=4), fw_version="openfw-0.3.2",
               payload={"OTA": "applied", "Version": "openfw-0.3.2"})
    add_uplink(c, DEV, kind="dl_ack", received_at=now - timedelta(minutes=3),
               payload={"Downklink_Ack": "success"})
    add_sensor(c, DEV, now - timedelta(days=2), unit="m", range_low=0, range_high=10, label="well probe")
    for i in range(1, 41):
        add_reading(c, DEV, now - timedelta(hours=i * 3), 8.0 + (i % 5))
    add_reading(c, DEV, now - timedelta(minutes=30), 3.0)  # fault sample


def test_html_requires_basic_auth(client, api_conn):
    seed(api_conn)
    for path in ("/", f"/devices/{DEV}"):
        r = client.get(path)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Basic"
        assert client.get(path, auth=(BASIC[0], "wrong")).status_code == 401
    # API bearer tokens do not open the HTML UI
    assert client.get("/", headers={"Authorization": "Bearer rw-token-for-tests"}).status_code == 401


def test_basic_password_unset_is_503(client, api_env):
    from dashboard.tests.api_support import set_env
    set_env(api_env, BASIC_AUTH_PASSWORD="")
    assert client.get("/", auth=("admin", "")).status_code == 503


def test_fleet_page_content(client, api_conn):
    seed(api_conn)
    add_device(api_conn, DEV2)  # no label, never seen, no data
    add_device(api_conn, "ps-cb-1", last_seen=utcnow())
    add_reading(api_conn, "ps-cb-1", utcnow() - timedelta(minutes=2), 12.345)
    r = client.get("/", auth=BASIC)
    assert r.status_code == 200
    html = r.text
    assert "Bench unit" in html and DEV in html
    assert "-0.625 m" in html  # newest sample (3.0 mA) scaled, with unit
    assert 'badge fault' in html
    assert "12.345 mA" in html            # uncalibrated: raw mA
    assert "3.512 V" in html and "openfw-0.3.2" in html
    assert "never-seen" in html and DEV2 in html
    assert "overflow-x: auto" in html      # table wrapper rule exists
    assert 'class="wrap"' in html
    assert "<script" not in html and "http://" not in html.replace("http://www.w3.org", "")


def test_fleet_latest_value_uses_calibration(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now)
    add_sensor(api_conn, DEV, now - timedelta(days=1), unit="kPa", range_low=0, range_high=100)
    add_reading(api_conn, DEV, now - timedelta(minutes=1), 12.0)
    html = client.get("/", auth=BASIC).text
    assert "50.00 kPa" in html
    assert 'badge ok' in html


def test_fleet_stale_banner(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now - timedelta(days=3))
    add_uplink(api_conn, DEV, received_at=now - timedelta(days=3))
    html = client.get("/", auth=BASIC).text
    assert "Ingest may be stalled" in html and 'role="alert"' in html
    add_uplink(api_conn, DEV, received_at=now - timedelta(minutes=10))
    assert "Ingest may be stalled" not in client.get("/", auth=BASIC).text


def test_fleet_banner_when_no_uplinks(client, api_conn):
    add_device(api_conn, DEV)
    html = client.get("/", auth=BASIC).text
    assert "Ingest may be stalled" in html and "No uplink is stored" in html


@pytest.mark.parametrize("rng", ["24h", "7d", "30d", "bogus"])
def test_device_page_renders(client, api_conn, rng):
    seed(api_conn)
    r = client.get(f"/devices/{DEV}", params={"range": rng}, auth=BASIC)
    assert r.status_code == 200
    html = r.text
    assert "Bench unit" in html
    assert '<svg class="chart"' in html
    assert "openfw-0.3.2" in html and "3.512 V" in html
    assert "Firmware &amp; downlink events" in html and "dl_ack" in html and "success" in html
    assert "well probe" in html and 'id="calibration"' in html
    assert f'action="/devices/{DEV}/sensors"' in html and f'action="/devices/{DEV}/label"' in html
    assert "<th>Kind</th>" in html  # raw uplinks table has a kind column
    for other in ("24h", "7d", "30d"):
        assert f"?range={other}" in html
    assert "<script" not in html


def test_device_page_uncalibrated_uses_raw_ma(client, api_conn):
    now = utcnow()
    add_device(api_conn, DEV, last_seen=now)
    add_reading(api_conn, DEV, now - timedelta(hours=1), 9.0)
    html = client.get(f"/devices/{DEV}", auth=BASIC).text
    assert "uncalibrated, raw mA" in html and "<svg" in html


def test_device_page_empty_and_unknown(client, api_conn):
    add_device(api_conn, DEV)
    r = client.get(f"/devices/{DEV}", auth=BASIC)
    assert r.status_code == 200 and "No readings" in r.text and "No uplinks stored" in r.text
    assert client.get("/devices/nope", auth=BASIC).status_code == 404


def test_device_page_escapes_user_text(client, api_conn):
    add_device(api_conn, DEV, label="<script>alert(1)</script>", last_seen=utcnow())
    r = client.get(f"/devices/{DEV}", params={"error": "<b>x</b>"}, auth=BASIC)
    assert "<script>alert(1)</script>" not in r.text and "<b>x</b>" not in r.text


def label_of(c, device_id=DEV):
    with c.cursor() as cur:
        cur.execute("SELECT label FROM devices WHERE id = %s", (device_id,))
        return cur.fetchone()["label"]


def test_post_label_with_matching_origin(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    r = client.post(f"/devices/{DEV}/label", data={"label": " Pump 3 "}, auth=BASIC,
                    headers=OWN, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/devices/{DEV}?notice=label-saved")
    assert label_of(api_conn) == "Pump 3"
    r = client.post(f"/devices/{DEV}/label", data={"label": ""}, auth=BASIC, headers=OWN,
                    follow_redirects=False)
    assert r.status_code == 303 and label_of(api_conn) is None
    # redirect target renders with the notice
    page = client.get(r.headers["location"].split("#")[0], auth=BASIC)
    assert "Label saved." in page.text


def test_post_accepts_referer_and_forwarded_host(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    kw = dict(auth=BASIC, follow_redirects=False)
    r = client.post(f"/devices/{DEV}/label", data={"label": "A"},
                    headers={"Referer": f"http://testserver/devices/{DEV}"}, **kw)
    assert r.status_code == 303
    r = client.post(f"/devices/{DEV}/label", data={"label": "B"},
                    headers={"Origin": "https://dash.example.com", "X-Forwarded-Host": "dash.example.com"}, **kw)
    assert r.status_code == 303 and label_of(api_conn) == "B"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://testserver.evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/x"},
        {},
    ],
)
def test_csrf_guard_rejects(client, api_conn, headers):
    add_device(api_conn, DEV, label="orig", last_seen=utcnow())
    for path, data in ((f"/devices/{DEV}/label", {"label": "pwned"}),
                       (f"/devices/{DEV}/sensors", {"unit": "m", "range_low": "0", "range_high": "1"})):
        r = client.post(path, data=data, auth=BASIC, headers=headers, follow_redirects=False)
        assert r.status_code == 403, (path, headers)
    assert label_of(api_conn) == "orig"
    with api_conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM sensors")
        assert cur.fetchone()["n"] == 0


def test_post_requires_basic_auth_before_csrf(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    r = client.post(f"/devices/{DEV}/label", data={"label": "x"}, headers=OWN)
    assert r.status_code == 401


def test_post_unknown_device_404(client, api_conn):
    assert client.post("/devices/nope/label", data={"label": "x"}, auth=BASIC, headers=OWN).status_code == 404
    assert client.post("/devices/nope/sensors", data={"unit": "m", "range_low": "0", "range_high": "1"},
                       auth=BASIC, headers=OWN).status_code == 404


def test_post_sensor_form_creates_period(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    data = {"valid_from": "2026-03-04T05:06", "channel": "idc", "kind": "level", "unit": "m",
            "in_low": "4", "in_high": "20", "range_low": "0", "range_high": "12.5", "offset": "-0.2",
            "label": "new probe"}
    r = client.post(f"/devices/{DEV}/sensors", data=data, auth=BASIC, headers=OWN, follow_redirects=False)
    assert r.status_code == 303 and "notice=sensor-added" in r.headers["location"]
    assert r.headers["location"].endswith("#calibration")
    with api_conn.cursor() as cur:
        cur.execute('SELECT valid_from, unit, range_high, "offset", label FROM sensors')
        row = cur.fetchone()
    assert row["valid_from"].isoformat().startswith("2026-03-04T05:06:00")
    assert (row["unit"], row["range_high"], row["offset"], row["label"]) == ("m", 12.5, -0.2, "new probe")
    page = client.get(r.headers["location"].split("#")[0], auth=BASIC).text
    assert "Calibration period added." in page and "new probe" in page


def test_post_sensor_form_defaults_blank_valid_from_and_blank_numbers(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    data = {"valid_from": "", "unit": "kPa", "in_low": "", "in_high": "", "offset": "",
            "range_low": "0", "range_high": "100"}
    r = client.post(f"/devices/{DEV}/sensors", data=data, auth=BASIC, headers=OWN, follow_redirects=False)
    assert "notice=sensor-added" in r.headers["location"]
    with api_conn.cursor() as cur:
        cur.execute('SELECT in_low, in_high, "offset", valid_from FROM sensors')
        row = cur.fetchone()
    assert (row["in_low"], row["in_high"], row["offset"]) == (4, 20, 0)
    assert abs((utcnow() - row["valid_from"]).total_seconds()) < 60


@pytest.mark.parametrize(
    "data,needle",
    [
        ({"unit": "", "range_low": "0", "range_high": "1"}, "unit"),
        ({"unit": "m", "range_low": "0"}, "range_high"),
        ({"unit": "m", "range_low": "x", "range_high": "1"}, "range_low"),
        ({"unit": "m", "range_low": "0", "range_high": "1", "in_low": "5", "in_high": "5"}, "in_high"),
        ({"unit": "m", "range_low": "0", "range_high": "1", "channel": "bad"}, "channel"),
    ],
)
def test_post_sensor_form_validation_shows_error(client, api_conn, data, needle):
    add_device(api_conn, DEV, last_seen=utcnow())
    r = client.post(f"/devices/{DEV}/sensors", data=data, auth=BASIC, headers=OWN, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    page = client.get(r.headers["location"].split("#")[0], auth=BASIC).text
    assert 'class="banner err"' in page and needle in page
    with api_conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM sensors")
        assert cur.fetchone()["n"] == 0


def test_post_sensor_form_duplicate_valid_from(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    data = {"valid_from": "2026-03-04T05:06", "unit": "m", "range_low": "0", "range_high": "1"}
    assert "sensor-added" in client.post(f"/devices/{DEV}/sensors", data=data, auth=BASIC, headers=OWN,
                                         follow_redirects=False).headers["location"]
    r = client.post(f"/devices/{DEV}/sensors", data=data, auth=BASIC, headers=OWN, follow_redirects=False)
    assert "error=" in r.headers["location"] and quote("already exists") in r.headers["location"].replace("+", "%20")


def test_oversized_form_rejected(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    r = client.post(f"/devices/{DEV}/label", data={"label": "x" * 20000}, auth=BASIC, headers=OWN)
    assert r.status_code == 413


def test_lifespan_startup_creates_schema(api_env, api_conn):
    from fastapi.testclient import TestClient
    from dashboard.web import app
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
