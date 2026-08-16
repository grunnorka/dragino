"""MQTT → Postgres ingest worker.

Run: python -m dashboard.ingest
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from dashboard.db import connect, ensure_schema, record_uplink
from dashboard.extract import device_id_from_topic, extract_common
from dashboard.settings import load_settings

log = logging.getLogger("dashboard.ingest")


def _make_client(client_id: str) -> mqtt.Client:
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    settings = load_settings(ingest_defaults=True)
    seed_ids = set(settings.device_ids)

    log.info(
        "ingest starting mqtt=%s:%s topic=%s db=%s seeds=%s",
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_topic,
        settings.database_url.split("@")[-1],
        ",".join(settings.device_ids),
    )

    conn = connect(settings.database_url)
    ensure_schema(conn, settings.device_ids)

    client_id = f"dashboard-ingest-{os.getpid()}"
    client = _make_client(client_id)
    if settings.mqtt_user and settings.mqtt_pass:
        client.username_pw_set(settings.mqtt_user, settings.mqtt_pass)

    stopping = False

    def handle_stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True
        log.info("shutdown requested")
        client.disconnect()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    def on_connect(client: mqtt.Client, _u: object, _f: object, rc: object, _p: object = None) -> None:
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        log.info("mqtt connect rc=%s", code)
        if code != 0:
            return
        client.subscribe(settings.mqtt_topic)
        log.info("subscribed %s", settings.mqtt_topic)

    def on_message(_c: mqtt.Client, _u: object, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        device_id = device_id_from_topic(topic)
        if not device_id:
            log.warning("ignore topic=%s (cannot parse device id)", topic)
            return
        received_at = datetime.now(timezone.utc)
        raw = msg.payload.decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                payload = {"_raw": payload}
        except json.JSONDecodeError:
            payload = {"_raw": raw}

        extracts = extract_common(payload) if isinstance(payload, dict) else {}
        try:
            record_uplink(
                conn,
                device_id=device_id,
                topic=topic,
                received_at=received_at,
                payload=payload,
                extracts=extracts,
                known_seed=device_id in seed_ids,
            )
            log.info(
                "uplink device=%s battery=%s signal=%s model=%s",
                device_id,
                extracts.get("battery"),
                extracts.get("signal"),
                extracts.get("model"),
            )
        except Exception:
            log.exception("failed to persist uplink device=%s", device_id)
            try:
                conn.rollback()
            except Exception:
                pass

    def on_disconnect(client: mqtt.Client, _u: object, _c: object, rc: object, _p: object = None) -> None:
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        log.warning("mqtt disconnect rc=%s", code)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    backoff = 1.0
    while not stopping:
        try:
            client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
            backoff = 1.0
            client.loop_forever()
        except Exception:
            if stopping:
                break
            log.exception("mqtt connection error; retry in %.1fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    try:
        conn.close()
    except Exception:
        pass
    log.info("ingest stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
