"""Inline SVG chart builder (pure function) + chart data selection."""
from __future__ import annotations

import re
from datetime import timedelta

from dashboard.charts import build_chart, fmt_num
from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import BASIC, DEV, add_device, add_reading, add_sensor, hour_floor, utcnow

T1 = utcnow()
T0 = T1 - timedelta(days=7)


def pts(spec, step=timedelta(hours=1)):
    base = T1 - step * len(spec)
    return [{"t": base + step * i, "v": v, "q": q} for i, (v, q) in enumerate(spec)]


def test_empty_returns_none():
    assert build_chart([], T0, T1, "m") is None
    assert build_chart([{"t": T1, "v": None, "q": None}], T0, T1, "m") is None


def test_min_max_latest_and_labels():
    c = build_chart(pts([(1.0, "ok"), (5.0, "ok"), (3.0, "ok")]), T0, T1, "m")
    assert (c["min"], c["max"], c["latest"], c["n"]) == (1.0, 5.0, 3.0, 3)
    svg = c["svg"]
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "max 5.00" in svg and "min 1.00" in svg and "latest 3.00" in svg
    assert svg.count('<path class="ln"') == 1


def test_latest_label_merges_with_max():
    c = build_chart(pts([(1.0, "ok"), (5.0, "ok")]), T0, T1, "m")
    assert "max / latest 5.00" in c["svg"]


def test_gap_splits_line():
    spec = [(1.0, "ok"), (2.0, "ok"), (3.0, "ok")]
    base = T1 - timedelta(days=2)
    points = [{"t": base + timedelta(hours=i), "v": v, "q": q} for i, (v, q) in enumerate(spec)]
    points += [{"t": base + timedelta(hours=30 + i), "v": 4.0 + i, "q": "ok"} for i in range(3)]
    c = build_chart(points, T0, T1, "m", bucket_seconds=3600)
    assert c["svg"].count('<path class="ln"') == 2 and c["gaps"] == 1


def test_bad_samples_are_coloured_points_and_break_line():
    spec = [(5.0, "ok"), (5.1, "ok"), (-2.5, "no_signal"), (5.2, "ok"), (5.3, "ok"),
            (4.9, "saturated"), (0.0, "fault")]
    c = build_chart(pts(spec), T0, T1, "m")
    svg = c["svg"]
    assert 'class="q-none' in svg and 'class="q-fault' in svg and 'class="q-sat' in svg
    assert c["bad"] == 3
    assert svg.count('<path class="ln"') == 2  # broken at the no_signal sample
    # off-scale bad points do not stretch the axis and are clamped
    assert c["min"] == 4.9 and c["max"] == 5.3
    assert "clamp" in svg
    ys = [float(y) for y in re.findall(r'cy="([\d.]+)"', svg)]
    assert all(0 <= y <= 280 for y in ys)


def test_flat_series_and_single_point():
    c = build_chart(pts([(3.0, "ok")] * 4), T0, T1, "m")
    assert c["min"] == c["max"] == 3.0 and "nan" not in c["svg"].lower()
    one = build_chart(pts([(3.0, "ok")]), T0, T1, "m")
    assert '<circle class="pt"' in one["svg"]


def test_unit_is_escaped():
    c = build_chart(pts([(1.0, "ok"), (2.0, "ok")]), T0, T1, "<b>")
    assert "<b>" not in c["svg"] and "&lt;b&gt;" in c["svg"]


def test_fmt_num():
    assert fmt_num(None) == "—"
    assert fmt_num(1234.5) == "1234"
    assert fmt_num(123.45) == "123.5"
    assert fmt_num(12.346) == "12.35"
    assert fmt_num(0.1234) == "0.123"


def test_chart_data_source_per_range(client, api_conn):
    h = hour_floor(utcnow())
    add_device(api_conn, DEV, last_seen=utcnow())
    add_sensor(api_conn, DEV, h - timedelta(days=40), unit="m", range_low=0, range_high=10)
    for i in range(0, 20 * 24, 4):  # 20 days, every 4 h
        add_reading(api_conn, DEV, h - timedelta(hours=i + 1), 12.0)
    r30 = client.get(f"/devices/{DEV}", params={"range": "30d"}, auth=BASIC).text
    assert "hourly averages" in r30
    r7 = client.get(f"/devices/{DEV}", params={"range": "7d"}, auth=BASIC).text
    assert "samples" in r7 and "hourly averages" not in r7
    assert "5.00 m" in r7


def test_dense_raw_falls_back_to_hourly(client, api_conn):
    h = hour_floor(utcnow())
    add_device(api_conn, DEV, last_seen=utcnow())
    with api_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO readings (device_id, t, source, idc_ma, vdc_v) "
            "SELECT %s, %s - make_interval(secs => g * 30), 'uplink', 10, 0 FROM generate_series(1, 2500) g",
            (DEV, h),
        )
    api_conn.commit()
    html = client.get(f"/devices/{DEV}", params={"range": "24h"}, auth=BASIC).text
    assert "too many samples, shown hourly" in html
