"""Pull common fields from Dragino JSON uplink payloads."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Sample times before this are an unset device clock (1970-01-01 etc.).
MIN_SAMPLE_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)

KINDS = ("uplink", "dl_ack", "ota", "status", "other")
_SENSOR_KEYS = ("idc_input", "vdc_input", "channel1_temp", "channel2_temp")


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or value == "NULL":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_str(value: Any) -> str | None:
    if value is None or value == "" or value == "NULL":
        return None
    text = str(value).strip()
    return text or None


def _as_time(value: Any) -> datetime | None:
    text = _as_str(value)
    if not text:
        return None
    # Dragino uses ISO-8601 with Z
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def classify_kind(payload: Any) -> str:
    """Message kind per API.md §1: uplink | dl_ack | ota | status | other.

    Acks, OTA reports and the status reply are recognised by their marker key
    first (they carry no sensor data); an uplink is anything with a sensor key
    (or a ``battery`` field); the rest (raw/non-JSON, datalog, ...) is ``other``.
    """
    if not isinstance(payload, dict):
        return "other"
    if "Downklink_Ack" in payload or "Downlink_Ack" in payload:
        return "dl_ack"
    if "OTA" in payload:
        return "ota"
    if "Image Version" in payload:
        return "status"
    if any(key in payload for key in _SENSOR_KEYS) or "battery" in payload:
        return "uplink"
    return "other"


def extract_fw_version(payload: dict[str, Any]) -> str | None:
    """``Version`` (OTA report) or ``Image Version`` (status reply)."""
    return _as_str(payload.get("Version")) or _as_str(payload.get("Image Version"))


def extract_common(payload: dict[str, Any]) -> dict[str, Any]:
    """Return battery, signal, imei, model, device_time, kind, fw_version."""
    return {
        "battery": _as_float(payload.get("battery")),
        "signal": _as_float(payload.get("signal")),
        "imei": _as_str(payload.get("IMEI") or payload.get("imei")),
        "model": _as_str(payload.get("Model") or payload.get("model")),
        "device_time": _as_time(payload.get("time") or payload.get("Time")),
        "kind": classify_kind(payload),
        "fw_version": extract_fw_version(payload),
    }


@dataclass(frozen=True)
class Reading:
    """One row for the ``readings`` table (before it gets a device/uplink id)."""

    t: datetime
    source: str  # 'uplink' | 'clocklog'
    idc_ma: float | None = None
    vdc_v: float | None = None
    temp1_c: float | None = None
    temp2_c: float | None = None


def _valid_sample_time(value: Any) -> datetime | None:
    dt = _as_time(value)
    if dt is None or dt < MIN_SAMPLE_TIME:
        return None
    return dt


def _clocklog_entries(payload: dict[str, Any]) -> list[tuple[int, Any]]:
    entries: list[tuple[int, Any]] = []
    for key, value in payload.items():
        if isinstance(key, str) and key.isdigit() and isinstance(value, (list, tuple)):
            entries.append((int(key), value))
    entries.sort(key=lambda item: item[0])
    return entries


def extract_readings(payload: dict[str, Any], received_at: datetime) -> list[Reading]:
    """Readings carried by a ``kind='uplink'`` message (empty for other kinds).

    * the uplink's own sample at ``time`` (``received_at`` when ``time`` is
      missing or before 2020); skipped when the message holds no value at all
    * one ``clocklog`` sample per numeric key ``"1"``, ``"2"``...:
      ``[idc_mA, vdc_V, "time"]`` (the probe variant ``[idc, vdc, converted,
      "time"]`` also works: time is the last element); entries with a
      missing/pre-2020 time or a malformed shape are skipped
    """
    if classify_kind(payload) != "uplink":
        return []
    out: list[Reading] = []
    own = Reading(
        t=_valid_sample_time(payload.get("time") or payload.get("Time")) or received_at,
        source="uplink",
        idc_ma=_as_float(payload.get("idc_input")),
        vdc_v=_as_float(payload.get("vdc_input")),
        temp1_c=_as_float(payload.get("channel1_temp")),
        temp2_c=_as_float(payload.get("channel2_temp")),
    )
    if any(v is not None for v in (own.idc_ma, own.vdc_v, own.temp1_c, own.temp2_c)):
        out.append(own)
    for _index, entry in _clocklog_entries(payload):
        if len(entry) < 3:
            continue
        t = _valid_sample_time(entry[-1])
        if t is None:
            continue
        idc, vdc = _as_float(entry[0]), _as_float(entry[1])
        if idc is None and vdc is None:
            continue
        out.append(Reading(t=t, source="clocklog", idc_ma=idc, vdc_v=vdc))
    return out


def model_slug(model: str | None) -> str | None:
    """Map payload Model (e.g. PS-CB, LTC2-CB) to a short slug."""
    text = _as_str(model)
    if not text:
        return None
    key = text.lower().replace("_", "-").split(",")[0].strip()
    if key.startswith("ps-cb") or key.startswith("pscb"):
        return "ps-cb"
    if key.startswith("ltc2"):
        return "ltc2"
    # Fallback: first hyphen segment (keeps unknown products readable)
    return key.split("-")[0] or None


def device_id_from_topic(topic: str) -> str | None:
    """Parse dragino/<device_id>/up → device_id (legacy / HEX fallback)."""
    parts = topic.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "dragino" and parts[-1] == "up":
        device_id = parts[1].strip()
        return device_id or None
    return None


def resolve_device_id(
    topic: str,
    extracts: dict[str, Any] | None = None,
) -> str | None:
    """Prefer ``{model}-{IMEI}`` from JSON payload; else topic segment.

    Same-model sensors share ``dragino/ps-cb/up`` (or ``ltc2``) topics, so the
    fleet must key on payload IMEI to tell units apart.
    """
    extracts = extracts or {}
    imei = _as_str(extracts.get("imei"))
    if imei:
        slug = model_slug(_as_str(extracts.get("model")))
        return f"{slug}-{imei}" if slug else imei
    return device_id_from_topic(topic)
