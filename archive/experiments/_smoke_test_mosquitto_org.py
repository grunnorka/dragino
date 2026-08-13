#!/usr/bin/env python3
"""PC smoke against test.mosquitto.org:1883."""
import socket
import time

import paho.mqtt.client as mqtt

host = "test.mosquitto.org"
port = 1883
print("tcp", socket.create_connection((host, port), timeout=10).getpeername(), flush=True)
got: list[bytes] = []


def on_connect(c, u, f, rc, p=None):
    code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
    print("CONNECT", code, flush=True)
    c.subscribe("dragino/ltc2/up")
    c.publish("dragino/ltc2/up", "pc-smoke-mosquitto-org")


def on_message(c, u, msg):
    got.append(msg.payload)
    print("MSG", msg.payload, flush=True)


try:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pc-smoke-mosq")
except Exception:
    c = mqtt.Client(client_id="pc-smoke-mosq")
c.on_connect = on_connect
c.on_message = on_message
c.connect(host, port, 60)
c.loop_start()
t = time.time() + 8
while time.time() < t and not got:
    time.sleep(0.2)
c.loop_stop()
c.disconnect()
print("RESULT", "OK" if got else "FAIL", flush=True)
