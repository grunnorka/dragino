"""Subscribe to Railway Mosquitto topics (default dragino/#). Ctrl+C to stop."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from railway_mqtt import load_config


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="dragino/#")
    ap.add_argument("--also-test", action="store_true", help="Also subscribe to test/#")
    args = ap.parse_args()
    cfg = load_config()
    host, port = cfg["MQTT_HOST"], int(cfg["MQTT_PORT"])
    # Unique per process so parallel listeners don't kick each other off the broker.
    client_id = f"railway-listen-{os.getpid()}"

    def on_connect(client, _u, _f, rc, _p=None):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        print(f"CONNECT {code} {host}:{port}", flush=True)
        if code != 0:
            return
        client.subscribe(args.topic)
        print(f"SUB {args.topic}", flush=True)
        if args.also_test:
            client.subscribe("test/#")
            print("SUB test/#", flush=True)

    def on_message(_c, _u, msg):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = msg.payload.decode("utf-8", "replace")
        print(f"{ts} {msg.topic} {body}", flush=True)

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except Exception:
        client = mqtt.Client(client_id=client_id)
    client.username_pw_set(cfg["MQTT_USER"], cfg["MQTT_PASS"])
    client.on_connect = on_connect
    client.on_message = on_message
    print(f"listening {host}:{port} …", flush=True)
    client.connect(host, port, 60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
