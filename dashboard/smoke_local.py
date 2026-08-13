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
os.environ.setdefault("DEVICE_IDS", "ps-cb,ltc2")

from fastapi.testclient import TestClient

from dashboard.db import connect, ensure_schema, list_fleet, record_uplink
from dashboard.extract import device_id_from_topic, extract_common
from dashboard.settings import load_settings
from dashboard.web import app


def main() -> int:
    assert device_id_from_topic("dragino/ps-cb/up") == "ps-cb"
    assert device_id_from_topic("nope") is None

    settings = load_settings()
    conn = connect(settings.database_url)
    ensure_schema(conn, settings.device_ids)

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

    record_uplink(
        conn,
        device_id="ps-cb",
        topic="dragino/ps-cb/up",
        received_at=datetime.now(timezone.utc),
        payload=payload,
        extracts=extracts,
        known_seed=True,
    )

    fleet = list_fleet(conn, settings.stale_after_hours)
    by_id = {d["id"]: d for d in fleet}
    assert "ps-cb" in by_id and "ltc2" in by_id
    assert by_id["ps-cb"]["status"] == "ok"
    assert by_id["ltc2"]["status"] == "never-seen"
    assert by_id["ltc2"]["stale"] is True
    conn.close()

    client = TestClient(app)
    bad = client.get("/")
    assert bad.status_code == 401

    ok = client.get("/", auth=("admin", "devpass"))
    assert ok.status_code == 200, ok.text
    assert "ps-cb" in ok.text and "ltc2" in ok.text
    assert "never-seen" in ok.text

    detail = client.get("/devices/ps-cb", auth=("admin", "devpass"))
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
