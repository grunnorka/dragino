"""Pull common fields from Dragino JSON uplink payloads."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or value == "NULL":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def extract_common(payload: dict[str, Any]) -> dict[str, Any]:
    """Return battery, signal, imei, model, device_time from payload."""
    return {
        "battery": _as_float(payload.get("battery")),
        "signal": _as_float(payload.get("signal")),
        "imei": _as_str(payload.get("IMEI") or payload.get("imei")),
        "model": _as_str(payload.get("Model") or payload.get("model")),
        "device_time": _as_time(payload.get("time") or payload.get("Time")),
    }


def device_id_from_topic(topic: str) -> str | None:
    """Parse dragino/<device_id>/up → device_id."""
    parts = topic.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "dragino" and parts[-1] == "up":
        device_id = parts[1].strip()
        return device_id or None
    return None
