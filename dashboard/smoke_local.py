"""Local smoke: schema + synthetic uplink + fleet HTML (no live MQTT required)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# Ensure repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://dragino:dragino@127.0.0.1:5432/dragino")
os.environ.setdefault("BASIC_AUTH_PASSWORD", "devpass")
os.environ["DEVICE_IDS"] = ""  # no topic-slug seeds; identity comes from IMEI

from fastapi.testclient import TestClient

from dashboard.db import connect, ensure_schema, list_fleet, record_uplink
from dashboard.extract import device_id_from_topic, extract_common, resolve_device_id
from dashboard.settings import load_settings
from dashboard.web import app


def main() -> int:
    assert device_id_from_topic("dragino/ps-cb/up") == "ps-cb"
    assert device_id_from_topic("nope") is None

    payload = {
        "IMEI": "869181074157262",
        "Model": "PS-CB",
        "idc_input": 4.031,
        "battery": 3.359,
        "signal": 25,
        "time": "2026-08-13T12:00:00Z",
    }
    extracts = extract_common(payload)
    assert extracts["battery"] == 3.359
    assert extracts["imei"] == "869181074157262"
    device_id = resolve_device_id("dragino/ps-cb/up", extracts)
    assert device_id == "ps-cb-869181074157262"
    # Without IMEI, fall back to topic slug
    assert resolve_device_id("dragino/ps-cb/up", {}) == "ps-cb"

    settings = load_settings()
    conn = connect(settings.database_url)
    ensure_schema(conn, settings.device_ids)

    record_uplink(
        conn,
        device_id=device_id,
        topic="dragino/ps-cb/up",
        received_at=datetime.now(timezone.utc),
        payload=payload,
        extracts=extracts,
        known_seed=False,
    )

    # Second unit, same topic, different IMEI
    payload2 = {**payload, "IMEI": "869181074157478", "battery": 3.321, "signal": 24}
    extracts2 = extract_common(payload2)
    device_id2 = resolve_device_id("dragino/ps-cb/up", extracts2)
    assert device_id2 == "ps-cb-869181074157478"
    record_uplink(
        conn,
        device_id=device_id2,
        topic="dragino/ps-cb/up",
        received_at=datetime.now(timezone.utc),
        payload=payload2,
        extracts=extracts2,
        known_seed=False,
    )

    fleet = list_fleet(conn, settings.stale_after_hours)
    by_id = {d["id"]: d for d in fleet}
    assert device_id in by_id and device_id2 in by_id
    assert by_id[device_id]["status"] == "ok"
    assert by_id[device_id2]["status"] == "ok"
    assert "ps-cb" not in by_id  # topic slug alone is not a device row
    conn.close()

    client = TestClient(app)
    bad = client.get("/")
    assert bad.status_code == 401

    ok = client.get("/", auth=("admin", "devpass"))
    assert ok.status_code == 200, ok.text
    assert device_id in ok.text and device_id2 in ok.text

    detail = client.get(f"/devices/{device_id}", auth=("admin", "devpass"))
    assert detail.status_code == 200
    assert "869181074157262" in detail.text
    assert "4.031" in detail.text

    missing = client.get("/devices/nope", auth=("admin", "devpass"))
    assert missing.status_code == 404

    print(json.dumps({"smoke": "ok", "fleet": [
        {"id": d["id"], "status": d["status"], "battery": d.get("battery")}
        for d in fleet
    ]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
