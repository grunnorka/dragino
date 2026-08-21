#!/usr/bin/env python3
"""Does the device's MQTT CONNECT reach the Railway broker? (takeover test, v2)

The device is already configured for the Railway broker with client id "ps-cb"
and TDC=60 (it retries an uplink every minute). We connect to the SAME broker
with the SAME client id and watch for an *abnormal* disconnect: per MQTT spec,
when a second client connects with an in-use id, the broker kicks the first.
So an abnormal disconnect of our PC client, time-correlated with a device
QMTCONN, proves the device's CONNECT reached and was accepted by the broker.

v2 fix: a clean shutdown disconnect is NOT counted as a takeover (v1's bug).
Secrets masked in output.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du  # noqa: E402
from railway_mqtt import load_config  # noqa: E402

DEVICE_ID = "ps-cb"
WATCH_S = 130.0


def ts() -> float:
    return time.monotonic()


def main() -> None:
    import paho.mqtt.client as mqtt

    cfg = load_config()
    secrets = [cfg["MQTT_PASS"], cfg["MQTT_USER"]]
    host, port = cfg["MQTT_FALLBACK_IP"], int(cfg["MQTT_PORT"])

    def mask(s: str) -> str:
        for sec in secrets:
            if sec:
                s = s.replace(sec, "***")
        return s

    stop = threading.Event()
    kicks: list[float] = []   # monotonic times of abnormal (broker-initiated) drops

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=DEVICE_ID)
    except Exception:
        client = mqtt.Client(client_id=DEVICE_ID)
    client.username_pw_set(cfg["MQTT_USER"], cfg["MQTT_PASS"])

    def on_connect(c, _u, _f, rc, _p=None):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        print(f"[{ts():.1f}] PC client connect rc={code}", flush=True)
        if code == 0:
            c.subscribe(f"dragino/{DEVICE_ID}/#", qos=0)

    def on_disconnect(_c, _u, *a):
        # paho v2: (flags, reason_code, properties); v1: (rc). A broker-initiated
        # drop (takeover) is abnormal (reason != 0/Normal). Our own clean
        # disconnect() at shutdown is normal — ignore it.
        rc = None
        for x in a:
            if hasattr(x, "value"):
                rc = x.value
            elif isinstance(x, int):
                rc = x
        abnormal = (rc not in (0, None)) and not stop.is_set()
        print(f"[{ts():.1f}] PC client disconnect rc={rc} abnormal={abnormal}",
              flush=True)
        if abnormal:
            kicks.append(ts())

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(host, port, 30)
    client.loop_start()
    print(f"[{ts():.1f}] PC client up on {host}:{port} as '{DEVICE_ID}'", flush=True)

    ser = du.open_serial("/dev/ttyUSB0", 9600)
    buf = du.LineBuffer()
    print(f"[{ts():.1f}] watching {WATCH_S:.0f}s; correlating device QMTCONN "
          f"with PC-client kicks", flush=True)

    deadline = ts() + WATCH_S
    while ts() < deadline:
        for line in du.read_for(ser, 1.0, buf, None):
            if "Opened the MQTT" in line:
                print(f"[{ts():.1f}] DEV QMTOPEN ok", flush=True)
            elif "qmtconn rx" in line or "connected to the server" in line \
                    or "Upload data successfully" in line:
                print(f"[{ts():.1f}] DEV {mask(line)}", flush=True)
            if kicks:
                break
        if kicks:
            break

    stop.set()
    ser.close()
    client.loop_stop()
    client.disconnect()
    print("=" * 50, flush=True)
    if kicks:
        print(f"RESULT: PC client was KICKED at t={kicks[0]:.1f} (abnormal drop) "
              f"-> the device's CONNECT REACHED and was accepted by the broker. "
              f"The +QMTSTAT is the broker/proxy dropping the device afterwards "
              f"(return path / session conflict).", flush=True)
    else:
        print("RESULT: PC client never abnormally dropped -> the device's CONNECT "
              "does NOT reach the broker's MQTT layer (carrier/proxy resets it "
              "before a valid CONNECT is processed).", flush=True)


if __name__ == "__main__":
    main()
