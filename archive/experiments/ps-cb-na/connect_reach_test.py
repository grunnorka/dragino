#!/usr/bin/env python3
"""Does the device's MQTT CONNECT reach the broker at all?

The device is currently configured (by ab_broker_test.py) for
test.mosquitto.org:1883, anonymous, CLIENT=pscb-ab, TDC=60 — it retries an
uplink every minute. We connect to the SAME broker with the SAME client id
"pscb-ab". Per MQTT spec, if the device's CONNECT arrives and is accepted,
the broker kicks the already-connected client (us). So:

  our PC client gets kicked  -> device CONNECT reached the broker
                                (problem is return path / timing)
  our PC client stays up      -> device CONNECT never reached the broker
                                (carrier / proxy / encoding drops it)

We also watch the device console to correlate the exact QMTCONN moment.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du  # noqa: E402

HOST = "54.36.178.49"     # test.mosquitto.org, IPv4 literal (sandbox has no v6)
PORT = 1883
CLIENT_ID = "pscb-ab"     # must match the device's AT+CLIENT
WATCH_S = 100.0


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    import paho.mqtt.client as mqtt

    kicked = threading.Event()
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
    except Exception:
        client = mqtt.Client(client_id=CLIENT_ID)

    def on_connect(c, _u, _f, rc, _p=None):
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        print(f"[{stamp()}] PC client connect rc={code}", flush=True)
        if code == 0:
            c.subscribe("dragino/#", qos=0)

    def on_disconnect(_c, _u, *a):
        print(f"[{stamp()}] *** PC client DISCONNECTED by broker "
              f"(device CONNECT took over the session) *** args={a}", flush=True)
        kicked.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(HOST, PORT, 30)
    client.loop_start()
    print(f"[{stamp()}] PC client connected to {HOST}:{PORT} as '{CLIENT_ID}'",
          flush=True)

    ser = du.open_serial("/dev/ttyUSB0", 9600)
    buf = du.LineBuffer()
    print(f"[{stamp()}] watching device console + broker for {WATCH_S:.0f}s",
          flush=True)

    deadline = time.monotonic() + WATCH_S
    while time.monotonic() < deadline and not kicked.is_set():
        for line in du.read_for(ser, 1.0, buf, None):
            if any(k in line for k in (
                    "Upload start", "Opened the MQTT", "connected to the server",
                    "QMTSTAT", "Failed to", "Upload data successfully")):
                print(f"[{stamp()}] DEV {line}", flush=True)

    ser.close()
    client.loop_stop()
    client.disconnect()
    print("=" * 50, flush=True)
    if kicked.is_set():
        print("RESULT: broker kicked our same-id client -> the device's CONNECT "
              "REACHES the broker. Failure is CONNACK return path / timing.",
              flush=True)
    else:
        print("RESULT: our same-id client was never kicked -> the device's "
              "CONNECT does NOT reach the broker (carrier/proxy/encoding).",
              flush=True)


if __name__ == "__main__":
    main()
