"""Smoke-test Railway Mosquitto via TCP proxy (pub/sub round-trip)."""
from __future__ import annotations

import argparse
import socket
import sys
import time

import paho.mqtt.client as mqtt

from railway_mqtt import load_config


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="test/ping")
    ap.add_argument("--payload", default="ok-from-pc")
    ap.add_argument("--timeout", type=float, default=12.0)
    args = ap.parse_args()
    cfg = load_config()
    host, port = cfg["MQTT_HOST"], int(cfg["MQTT_PORT"])
    user, password = cfg["MQTT_USER"], cfg["MQTT_PASS"]

    print(f"resolve {host}:{port} ->", socket.getaddrinfo(host, port)[0][4], flush=True)
    with socket.create_connection((host, port), timeout=10) as s:
        print("tcp_ok", s.getpeername(), flush=True)

    got: list[tuple[str, str]] = []

    def on_connect(client, _u, _f, rc, _p=None):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        print("CONNECT", code, flush=True)
        if code == 0:
            client.subscribe(args.topic)
            client.publish(args.topic, args.payload)

    def on_message(_c, _u, msg):
        text = msg.payload.decode("utf-8", "replace")
        got.append((msg.topic, text))
        print("MSG", msg.topic, text, flush=True)

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="railway-smoke")
    except Exception:
        client = mqtt.Client(client_id="railway-smoke")
    client.username_pw_set(user, password)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, 60)
    client.loop_start()
    deadline = time.time() + args.timeout
    while time.time() < deadline and not got:
        time.sleep(0.2)
    client.loop_stop()
    client.disconnect()

    ok = any(t == args.topic and p == args.payload for t, p in got)
    print("RESULT", "OK" if ok else "FAIL", got)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
