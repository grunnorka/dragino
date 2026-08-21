#!/usr/bin/env python3
"""Configure PS-CB-NA for Railway MQTT, then watch serial + broker together.

Unlocks during the boot window (the device sleeps between uplinks, so a fresh
RESET is the reliable moment to get AT access), applies the JSON-MQTT profile,
verifies the read-back, then forces an uplink and confirms it arrives on the
broker. All hardware actions are requested via on-screen popups.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import prompt_user  # noqa: E402
from dragino_uart import (  # noqa: E402
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    send_line,
    unlock,
)
from railway_mqtt import load_config  # noqa: E402

DEVICE_ID = "ps-cb"
PUB_TOPIC = f"dragino/{DEVICE_ID}/up"
SUB_TOPIC = f"dragino/{DEVICE_ID}/down"

VERIFY_QUERIES = [
    ("pro", "AT+PRO=?"),
    ("servaddr", "AT+SERVADDR=?"),
    ("bkdns", "AT+BKDNS=?"),
    ("client", "AT+CLIENT=?"),
    ("uname", "AT+UNAME=?"),
    ("pubtopic", "AT+PUBTOPIC=?"),
    ("subtopic", "AT+SUBTOPIC=?"),
    ("tlsmod", "AT+TLSMOD=?"),
    ("tdc", "AT+TDC=?"),
]


class BrokerWatch:
    """Background subscriber that records messages on the device's uplink topic."""

    def __init__(self, cfg: dict[str, str]) -> None:
        self.cfg = cfg
        self.messages: list[tuple[str, str, str]] = []
        self.connected = threading.Event()
        self._client = None

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client_id = f"pscb-setup-{os.getpid()}"
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except Exception:
            client = mqtt.Client(client_id=client_id)
        client.username_pw_set(self.cfg["MQTT_USER"], self.cfg["MQTT_PASS"])

        def on_connect(_c, _u, _f, rc, _p=None):
            code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
            if code == 0:
                client.subscribe(f"dragino/{DEVICE_ID}/#", qos=1)
                self.connected.set()

        def on_message(_c, _u, msg):
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            body = msg.payload.decode("utf-8", "replace")
            self.messages.append((stamp, msg.topic, body))
            print(f"  [BROKER {stamp}] {msg.topic} {body}", flush=True)

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(self.cfg["MQTT_HOST"], int(self.cfg["MQTT_PORT"]), 60)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--tdc", type=int, default=180)
    parser.add_argument("--use-ip", action="store_true", default=True)
    parser.add_argument("--uplink-wait", type=float, default=420.0)
    args = parser.parse_args()

    pin = resolve_pin()
    if not pin:
        raise SystemExit("No PIN found. Set DRAGINO_PIN in .env")
    cfg = load_config()
    if not cfg.get("MQTT_PASS"):
        raise SystemExit("No MQTT_PASS. Create railway-mqtt.local.env first.")
    secrets = [pin, cfg["MQTT_PASS"]]

    host = cfg["MQTT_FALLBACK_IP"] if args.use_ip else cfg["MQTT_HOST"]
    addr = f"{host},{cfg['MQTT_PORT']}"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logpath = logdir / f"{stamp}_pscb_railway_setup.log"
    log = logpath.open("w", encoding="utf-8")

    def out(text: str) -> None:
        safe = text
        for secret in secrets:
            if secret:
                safe = safe.replace(secret, "***")
        print(safe, flush=True)
        log.write(safe + "\n")
        log.flush()

    apply_cmds = [
        ("AT+PRO=3,5", 3.0),
        ("AT+TLSMOD=0,0", 2.0),
        (f"AT+SERVADDR={addr}", 2.5),
        (f"AT+BKDNS=1,0,{addr}", 2.5),
        (f"AT+CLIENT={DEVICE_ID}", 2.0),
        (f"AT+UNAME={cfg['MQTT_USER']}", 2.0),
        (f"AT+PWD={cfg['MQTT_PASS']}", 2.0),
        (f"AT+PUBTOPIC={PUB_TOPIC}", 2.0),
        (f"AT+SUBTOPIC={SUB_TOPIC}", 2.0),
        ("AT+MQOS=1", 1.5),
        (f"AT+TDC={args.tdc}", 1.5),
        # AT+PRO can rewrite defaults, so re-assert the broker details after it.
        (f"AT+SERVADDR={addr}", 2.0),
        (f"AT+BKDNS=1,0,{addr}", 2.0),
        (f"AT+UNAME={cfg['MQTT_USER']}", 1.5),
        (f"AT+PWD={cfg['MQTT_PASS']}", 1.5),
        (f"AT+CLIENT={DEVICE_ID}", 1.5),
    ]

    watch = BrokerWatch(cfg)
    watch.start()
    if watch.connected.wait(timeout=15):
        out(f"broker subscriber connected to {cfg['MQTT_HOST']}:{cfg['MQTT_PORT']}")
    else:
        out("WARNING: broker subscriber did not connect; serial-only verification")

    prompt_user.step(
        "Reboot the sensor so I can log in",
        [
            "SW1 must be in the Flash (normal) position.",
            "",
            "1. Click the button below.",
            "2. Then press RESET on the board.",
            "",
            "I will log in with the PIN during the boot window and write",
            "the Railway MQTT settings. This takes a couple of minutes.",
        ],
        ok_label="Ready - I will press RESET now",
    )

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()
    out("=== waiting for boot and unlocking ===")
    result = unlock(ser, pin, policy="stable", timeout=220.0, on_line=out, on_tx=out)
    out(f"unlock ok={result.ok} phase={result.phase.value} hint={result.hint}")
    if not result.ok:
        ser.close()
        watch.stop()
        log.close()
        raise SystemExit(f"Could not unlock; see {logpath}")

    out("\n=== applying Railway MQTT config ===")
    for cmd, wait in apply_cmds:
        out(f">>> {cmd}")
        send_line(ser, cmd)
        read_for(ser, wait, buf, out)

    out("\n=== reading config back ===")
    readback: dict[str, str] = {}
    for key, cmd in VERIFY_QUERIES:
        out(f">>> {cmd}")
        send_line(ser, cmd)
        for line in read_for(ser, 2.0, buf, out):
            text = line.strip()
            if not text or text == "OK" or text.startswith("[") or text.startswith("AT+"):
                continue
            readback.setdefault(key, text)

    checks = {
        "PRO is 3,5 (JSON MQTT)": readback.get("pro", "").startswith("3,5"),
        "SERVADDR is the Railway proxy": (
            host in readback.get("servaddr", "")
            and cfg["MQTT_PORT"] in readback.get("servaddr", "")
        ),
        "BKDNS points at Railway": host in readback.get("bkdns", ""),
        "no HiveMQ leftovers": "hivemq" not in str(readback).lower(),
        "CLIENT is ps-cb": readback.get("client") == DEVICE_ID,
        "UNAME matches broker user": readback.get("uname") == cfg["MQTT_USER"],
        "PUBTOPIC correct": readback.get("pubtopic") == PUB_TOPIC,
        "TLS off": readback.get("tlsmod", "").startswith("0,0"),
        f"TDC is {args.tdc}": readback.get("tdc", "").strip() == str(args.tdc),
    }
    out("\n=== config check ===")
    for label, ok in checks.items():
        out(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    out(f"  read back: {readback}")

    prompt_user.step(
        "Force an uplink",
        [
            "Press and hold the ACT button for 1 to 3 seconds, then release.",
            "(A short press only - do not long-press, that starts OTA.)",
            "",
            "Click the button below first, then press ACT.",
            "",
            "I will watch the serial port and the broker at the same time",
            f"for up to {int(args.uplink_wait / 60)} minutes.",
        ],
        ok_label="Ready - I will press ACT now",
    )

    out("\n=== watching for uplink (serial + broker) ===")
    upload_ok = False
    connected_ok = False
    deadline = time.time() + args.uplink_wait
    while time.time() < deadline:
        for line in read_for(ser, 1.0, buf, out):
            if "Successfully connected to the server" in line:
                connected_ok = True
            if "Upload data successfully" in line:
                upload_ok = True
        if upload_ok and watch.messages:
            read_for(ser, 10.0, buf, out)
            break

    ser.close()
    time.sleep(2)
    watch.stop()

    out("\n=== SUMMARY ===")
    out(f"config checks passed: {sum(checks.values())}/{len(checks)}")
    out(f"serial 'Successfully connected to the server': {connected_ok}")
    out(f"serial 'Upload data successfully': {upload_ok}")
    out(f"broker messages received: {len(watch.messages)}")
    for stamp_, topic, body in watch.messages:
        out(f"  {stamp_} {topic} {body}")
    out(f"log: {logpath}")
    log.close()

    if not (upload_ok and watch.messages):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
