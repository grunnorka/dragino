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
