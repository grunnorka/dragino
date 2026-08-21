#!/usr/bin/env python3
"""Diagnose and repair the PS-CB-NA MQTT parameters, then prove an uplink.

Reads the authoritative AT+CFG dump, reports every MQTT field, writes only what
is wrong (each command acknowledged individually), reboots with ATZ because
AT+PRO needs it, re-checks persistence, and watches the boot-time upload on both
the serial port and the broker.
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
from at_session import at_cmd, is_unset, read_cfg  # noqa: E402
from dragino_uart import (  # noqa: E402
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    unlock,
)
from railway_mqtt import load_config  # noqa: E402

DEVICE_ID = "ps-cb"
PUB_TOPIC = f"dragino/{DEVICE_ID}/up"
SUB_TOPIC = f"dragino/{DEVICE_ID}/down"

# AT+CFG keys that decide whether the MQTT client can be configured at all.
MQTT_KEYS = [
    "PRO",
    "SERVADDR",
    "CLIENT",
    "UNAME",
    "PWD",
    "PUBTOPIC",
    "SUBTOPIC",
    "TLSMOD",
    "MQOS",
    "BKDNS",
    "TDC",
    "APN",
    "IPTYPE",
]


class BrokerWatch:
    def __init__(self, cfg: dict[str, str]) -> None:
        self.cfg = cfg
        self.messages: list[tuple[str, str, str]] = []
        self.connected = threading.Event()
        self._client = None

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client_id = f"pscb-fix-{os.getpid()}"
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
            print(f"  >>> BROKER GOT {stamp} {msg.topic} {body}", flush=True)

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
    parser.add_argument(
        "--host-mode",
        choices=["ip", "dns"],
        default="ip",
        help="use the Railway fallback IP or the proxy hostname for SERVADDR",
    )
    parser.add_argument("--boot-wait", type=float, default=260.0)
    parser.add_argument(
        "--apn",
        default="",
        help=(
            "carrier APN to set; use CLEAR to force AT+APN=NULL so the network "
            "assigns one (correct for the Vodafone GDSP SIM in this unit)"
        ),
    )
    args = parser.parse_args()

    pin = resolve_pin()
    if not pin:
        raise SystemExit("No PIN found. Set DRAGINO_PIN in .env")
    mqtt_cfg = load_config()
    if not mqtt_cfg.get("MQTT_PASS"):
        raise SystemExit("No MQTT_PASS. Create railway-mqtt.local.env first.")

    host = (
        mqtt_cfg["MQTT_FALLBACK_IP"] if args.host_mode == "ip" else mqtt_cfg["MQTT_HOST"]
    )
    port = mqtt_cfg["MQTT_PORT"]
    addr = f"{host},{port}"
    secrets = [pin, mqtt_cfg["MQTT_PASS"]]

    # key -> (wanted value as AT+CFG reports it, command to set it)
    desired = {
        "PRO": ("3,5", "AT+PRO=3,5"),
        "SERVADDR": (addr, f"AT+SERVADDR={addr}"),
        "CLIENT": (DEVICE_ID, f"AT+CLIENT={DEVICE_ID}"),
        "UNAME": (mqtt_cfg["MQTT_USER"], f"AT+UNAME={mqtt_cfg['MQTT_USER']}"),
        "PWD": (mqtt_cfg["MQTT_PASS"], f"AT+PWD={mqtt_cfg['MQTT_PASS']}"),
        "PUBTOPIC": (PUB_TOPIC, f"AT+PUBTOPIC={PUB_TOPIC}"),
        "SUBTOPIC": (SUB_TOPIC, f"AT+SUBTOPIC={SUB_TOPIC}"),
        "TLSMOD": ("0,0", "AT+TLSMOD=0,0"),
        "MQOS": ("1", "AT+MQOS=1"),
        "BKDNS": (f"1,0,{addr}", f"AT+BKDNS=1,0,{addr}"),
        "TDC": (str(args.tdc), f"AT+TDC={args.tdc}"),
    }
    if args.apn == "CLEAR":
        # Explicit NULL is the unset state; AT+APN= (empty) is a different, worse
        # setting on this firmware — AT+CFG must report AT+APN=NULL.
        desired["APN"] = ("NULL", "AT+APN=NULL")
    elif args.apn:
        desired["APN"] = (args.apn, f"AT+APN={args.apn}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logpath = logdir / f"{stamp}_pscb_mqtt_fix.log"
    log = logpath.open("w", encoding="utf-8")

    def out(text: str) -> None:
        safe = text
        for secret in secrets:
            if secret:
                safe = safe.replace(secret, "***")
        print(safe, flush=True)
        log.write(safe + "\n")
        log.flush()

    def report(cfg: dict[str, str], title: str) -> None:
        out(f"\n--- {title} ---")
        for key in MQTT_KEYS:
            value = cfg.get(key)
            shown = "<absent>" if value is None else (value or "<empty>")
            if key == "PWD" and value and not is_unset(value):
                shown = "***"
            flag = ""
            if key in desired:
                want = desired[key][0]
                flag = " OK" if (value or "").strip() == want else f" WANT {want}"
                if key == "PWD":
                    flag = " OK" if (value or "").strip() == want else " WANT ***"
            out(f"  {key:9} = {shown}{flag}")

    watch = BrokerWatch(mqtt_cfg)
    watch.start()
    out(
        f"broker subscriber: "
        f"{'connected' if watch.connected.wait(15) else 'NOT CONNECTED'} "
        f"({mqtt_cfg['MQTT_HOST']}:{port})"
    )

    prompt_user.step(
        "Reboot the sensor so I can log in",
        [
            "SW1 must be in the Flash (normal) position.",
            "",
            "1. Click the button below.",
            "2. Then press RESET on the board.",
            "",
            "I will read the current settings, fix the wrong ones and",
            "reboot the sensor. Expect this to run about 8 minutes with",
            "no further action needed from you.",
        ],
        ok_label="Ready - I will press RESET now",
    )

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()

    def login(timeout: float) -> bool:
        result = unlock(ser, pin, policy="stable", timeout=timeout, on_line=out, on_tx=out)
        out(f"unlock ok={result.ok} phase={result.phase.value} hint={result.hint}")
        return result.ok

    out("=== waiting for boot, then logging in ===")
    if not login(220.0):
        ser.close()
        watch.stop()
        log.close()
        raise SystemExit(f"Could not unlock; see {logpath}")

    before = read_cfg(ser, buf, out)
    out(f"\nparsed {len(before)} settings from AT+CFG")
    report(before, "MQTT parameters BEFORE")

    out("\n=== writing the parameters that are wrong ===")
    failures: list[str] = []
    for key, (want, command) in desired.items():
        current = before.get(key, "")
        if current.strip() == want:
            out(f"  {key}: already correct, skipping")
            continue
        acked, payload = at_cmd(ser, command, buf, out, timeout=12.0)
        note = " ".join(payload) if payload else ""
        out(f"  {key}: {'ACK' if acked else 'NO ACK'} {note}".rstrip())
        if not acked:
            failures.append(key)
    if failures:
        out(f"WARNING: no acknowledgement for: {', '.join(failures)}")

    after = read_cfg(ser, buf, out)
    report(after, "MQTT parameters AFTER writing (before reboot)")

    out("\n=== ATZ reboot so AT+PRO takes effect ===")
    at_cmd(ser, "ATZ", buf, out, timeout=8.0)
    out(f"watching boot and first upload for up to {args.boot_wait:.0f}s")
    upload_ok = False
    connected_ok = False
    param_error = False
    deadline = time.time() + args.boot_wait
    while time.time() < deadline:
        for line in read_for(ser, 1.0, buf, out):
            if "Successfully connected to the server" in line:
                connected_ok = True
            if "Upload data successfully" in line:
                upload_ok = True
            if "parameter configuration error" in line:
                param_error = True
        if upload_ok and watch.messages:
            read_for(ser, 15.0, buf, out)
            break

    out("\n=== persistence check after reboot ===")
    persisted: dict[str, str] = {}
    if login(180.0):
        persisted = read_cfg(ser, buf, out)
        report(persisted, "MQTT parameters AFTER reboot")
    else:
        out("could not log in again to confirm persistence")

    ser.close()
    time.sleep(2)
    watch.stop()

    out("\n=== SUMMARY ===")
    out(f"serial 'Successfully connected to the server': {connected_ok}")
    out(f"serial 'Upload data successfully': {upload_ok}")
    out(f"serial 'parameter configuration error' seen: {param_error}")
    out(f"broker messages received: {len(watch.messages)}")
    for stamp_, topic, body in watch.messages:
        out(f"  {stamp_} {topic} {body}")
    out(f"log: {logpath}")
    log.close()
    if not (upload_ok and watch.messages):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
