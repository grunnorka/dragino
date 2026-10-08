from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dashboard.extract import (
    Reading,
    classify_kind,
    extract_common,
    extract_fw_version,
    extract_readings,
    resolve_device_id,
)
from dashboard.tests.conftest import IMEI, NOW, uplink_payload

UTC = timezone.utc


def test_classify_kinds() -> None:
    assert classify_kind(uplink_payload()) == "uplink"
    assert classify_kind({"IMEI": IMEI, "Model": "PS-CB", "channel1_temp": 21.5}) == "uplink"
    assert classify_kind({"IMEI": IMEI, "Downklink_Ack": "success"}) == "dl_ack"
    assert classify_kind({"IMEI": IMEI, "Downlink_Ack": "success"}) == "dl_ack"
    assert classify_kind({"IMEI": IMEI, "Downklink_Ack": "error", "Error": "12VT:FORMAT"}) == "dl_ack"
    assert classify_kind({"IMEI": IMEI, "OTA": "applied", "Version": "openfw-0.3.2"}) == "ota"
    status = {"IMEI": IMEI, "Image Version": "openfw-0.3.2", "NB-IoT Stack": "x", "Model": "PS-CB"}
    assert classify_kind(status) == "status"
    assert classify_kind({"IMEI": IMEI, "Message_Type": "Datalog"}) == "other"
    assert classify_kind({"_raw": "not json"}) == "other"
    assert classify_kind("nope") == "other"


def test_fw_version() -> None:
    assert extract_fw_version({"OTA": "applied", "Version": "openfw-0.3.2"}) == "openfw-0.3.2"
    assert extract_fw_version({"Image Version": "openfw-0.3.1"}) == "openfw-0.3.1"
    assert extract_fw_version(uplink_payload()) is None
    assert extract_common({"IMEI": IMEI, "OTA": "failed", "Version": "v9"})["fw_version"] == "v9"


def test_resolve_device_id() -> None:
    ex = extract_common(uplink_payload())
    assert resolve_device_id("dragino/ps-cb/up", ex) == f"ps-cb-{IMEI}"
    ack = extract_common({"IMEI": IMEI, "Downklink_Ack": "success"})
    assert resolve_device_id("dragino/ps-cb/up", ack) == IMEI  # DB layer maps it to the real device
    assert resolve_device_id("dragino/ps-cb/up", {}) == "ps-cb"


def test_uplink_and_clocklog_readings() -> None:
    rs = extract_readings(uplink_payload(), NOW)
    assert rs[0] == Reading(
        t=datetime(2026, 10, 8, 10, 31, 2, tzinfo=UTC), source="uplink", idc_ma=3.962, vdc_v=0.0
    )
    assert [(r.source, r.t.hour, r.idc_ma) for r in rs[1:]] == [
        ("clocklog", 8, 3.961),
        ("clocklog", 6, 3.962),
    ]


def test_clocklog_keys_sorted_numerically_and_bad_entries_skipped() -> None:
    payload = uplink_payload(
        **{
            "10": [4.1, 0.0, "2026-10-08T01:00:00Z"],
            "3": [4.0, 0.0, "1970-01-01T00:00:00Z"],  # unset RTC
            "4": [4.0, 0.0, "garbage"],
            "5": [4.0, 0.0],  # too short
            "6": ["NULL", "", "2026-10-08T02:00:00Z"],  # no value
            "7": [4.2, 0.0, 3.3, "2026-10-08T03:00:00Z"],  # probe variant: converted value in between
        }
    )
    rs = extract_readings(payload, NOW)
    clock = [r for r in rs if r.source == "clocklog"]
    assert [(r.t.hour, r.idc_ma) for r in clock] == [(8, 3.961), (6, 3.962), (3, 4.2), (1, 4.1)]


def test_null_values_and_time_fallback() -> None:
    payload = uplink_payload(idc_input="NULL", vdc_input="", time="1970-01-01T00:00:03Z")
    payload = {k: v for k, v in payload.items() if k not in ("1", "2")}
    assert extract_readings(payload, NOW) == []  # nothing numeric -> no row

    payload["vdc_input"] = 12.5
    (r,) = extract_readings(payload, NOW)
    assert (r.t, r.idc_ma, r.vdc_v) == (NOW, None, 12.5)  # pre-2020 time -> received_at

    del payload["time"]
    assert extract_readings(payload, NOW)[0].t == NOW


def test_ltc2_temperatures() -> None:
    payload = {
        "IMEI": IMEI,
        "Model": "PS-CB",
        "channel1_temp": -327.6,
        "channel2_temp": 22.4,
        "battery": 3.499,
        "time": "2026-10-08T10:00:00Z",
    }
    (r,) = extract_readings(payload, NOW)
    assert (r.idc_ma, r.vdc_v, r.temp1_c, r.temp2_c) == (None, None, -327.6, 22.4)


@pytest.mark.parametrize("payload", [
    {"IMEI": IMEI, "Downklink_Ack": "success"},
    {"IMEI": IMEI, "OTA": "applied", "Version": "x"},
    {"_raw": "x"},
])
def test_non_uplink_has_no_readings(payload: dict) -> None:
    assert extract_readings(payload, NOW) == []


def test_non_finite_numbers_are_null() -> None:
    payload = uplink_payload(idc_input=float("nan"), vdc_input=1.5, **{"1": 5})  # "1" not a list
    del payload["2"]
    (r,) = extract_readings(payload, NOW)
    assert (r.idc_ma, r.vdc_v) == (None, 1.5)
