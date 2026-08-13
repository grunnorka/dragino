#!/usr/bin/env python3
"""Bridge MQTT: test.mosquitto.org -> Railway Mosquitto (same topics)."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    load_env(ROOT / "railway-mqtt.local.env")
    src_host = "test.mosquitto.org"
    src_port = 1883
    dst_host = os.environ.get("MQTT_HOST", "altaria.proxy.rlwy.net")
    dst_port = int(os.environ.get("MQTT_PORT", "33239"))
    user = os.environ["MQTT_USER"]
    password = os.environ["MQTT_PASS"]
    topic = "dragino/ltc2/#"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_mqtt_bridge_mosqorg_railway.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {msg}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    try:
        src = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bridge-src-ltc2")
        dst = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bridge-dst-ltc2")
    except Exception:
        src = mqtt.Client(client_id="bridge-src-ltc2")
        dst = mqtt.Client(client_id="bridge-dst-ltc2")

    dst.username_pw_set(user, password)
    stats = {"fwd": 0}

    def on_src_connect(c, u, f, rc, p=None):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        log(f"SRC_CONNECT {code}")
        if code == 0:
            c.subscribe(topic)

    def on_dst_connect(c, u, f, rc, p=None):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        log(f"DST_CONNECT {code}")

    def on_src_message(c, u, msg):
        payload = msg.payload
        info = dst.publish(msg.topic, payload, qos=1)
        stats["fwd"] += 1
        log(
            f"FWD n={stats['fwd']} topic={msg.topic} bytes={len(payload)} mid={getattr(info, 'mid', '?')}"
        )

    src.on_connect = on_src_connect
    src.on_message = on_src_message
    dst.on_connect = on_dst_connect

    log(f"START src={src_host}:{src_port} dst={dst_host}:{dst_port} topic={topic} log={logpath}")
    dst.connect(dst_host, dst_port, 60)
    src.connect(src_host, src_port, 60)
    dst.loop_start()
    src.loop_start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("STOP")
    finally:
        src.loop_stop()
        dst.loop_stop()
        src.disconnect()
        dst.disconnect()


if __name__ == "__main__":
    main()
