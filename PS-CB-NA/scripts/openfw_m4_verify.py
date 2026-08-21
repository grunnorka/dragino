#!/usr/bin/env python3
"""M4 end-to-end verification for the PS-CB-NA open firmware MQTT uplink.

Flow: RESET -> unlock console -> apply the Railway broker settings (from
railway-mqtt.local.env / .env, never printed) -> ATZ -> capture boot +
uplink cycles while a paho subscriber watches dragino/ps-cb/up on the broker.

Success criteria (both sides):
  * console shows the MODEM.md section-0 contract sequence ending in
    "Upload data successfully"
  * the broker receives valid JSON on dragino/ps-cb/up

Usage: .venv/bin/python PS-CB-NA/scripts/openfw_m4_verify.py [--tdc 60] [--watch 300]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du  # noqa: E402
from railway_mqtt import load_config  # noqa: E402

DEVICE_ID = "ps-cb"
PUB_TOPIC = f"dragino/{DEVICE_ID}/up"
SUB_TOPIC = f"dragino/{DEVICE_ID}/down"

t0 = time.monotonic()
logf = None
secrets: list[str] = []


def out(text: str) -> None:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "***")
    stamp = f"{time.monotonic() - t0:9.3f}"
    line = f"[{stamp}] {safe}"
    print(line, flush=True)
    logf.write(line + "\n")
    logf.flush()


class BrokerWatch:
    def __init__(self, cfg: dict[str, str]) -> None:
        self.cfg = cfg
        self.messages: list[tuple[str, str]] = []
        self.connected = threading.Event()
        self._client = None

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=f"pscb-m4-verify-{os.getpid()}")
        except Exception:
            client = mqtt.Client(client_id=f"pscb-m4-verify-{os.getpid()}")
        client.username_pw_set(self.cfg["MQTT_USER"], self.cfg["MQTT_PASS"])

        def on_connect(_c, _u, _f, rc, _p=None):
            code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
            if code == 0:
                client.subscribe(f"dragino/{DEVICE_ID}/#", qos=1)
                self.connected.set()

        def on_message(_c, _u, msg):
            body = msg.payload.decode("utf-8", "replace")
            self.messages.append((msg.topic, body))
            out(f">>> BROKER RX {msg.topic} {body}")

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(self.cfg["MQTT_HOST"], int(self.cfg["MQTT_PORT"]), 60)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()


def send_and_log(ser, buf, cmd: str, timeout: float = 6.0) -> list[str]:
    out(f"TX {cmd}")
    du.send_line(ser, cmd)
    lines = du.read_for(ser, timeout, buf, None)
    for line in lines:
        out(f"RX {line}")
    return lines


def unlock(ser, buf, pin: str, tries: int = 6) -> bool:
    """PIN exchange. Since commit 60d9b27 the PIN gates nothing but still
    replies 'Password Correct'; on older flashes an already-unlocked console
    replies ERROR to a bare PIN. Accept either, then confirm with AT->OK."""
    for _ in range(tries):
        du.send_line(ser, pin)
        saw_error = False
        for line in du.read_for(ser, 2.0, buf, None):
            out(f"RX {line}")
            if du.RE_PASSWORD_OK.search(line):
                return True
            if line.strip() == "ERROR":
                saw_error = True
        if saw_error:
            du.send_line(ser, "AT")
            for line in du.read_for(ser, 2.0, buf, None):
                out(f"RX {line}")
                if line.strip() == "OK":
                    out("console already open (no PIN gate); proceeding")
                    return True
    return False


def send_acked(ser, buf, pin: str, cmd: str, tries: int = 3) -> bool:
    """Send a config command; on ERROR/timeout re-unlock (a boot may have
    reset the console session) and retry. Returns True on an OK."""
    for attempt in range(tries):
        lines = send_and_log(ser, buf, cmd)
        if any(l.strip() == "OK" for l in lines):
            return True
        out(f"cmd not ACKed (attempt {attempt + 1}); re-unlocking")
        if not unlock(ser, buf, pin):
            out("re-unlock failed")
    return False


def main() -> None:
    global logf
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    ap.add_argument("--tdc", type=int, default=60)
    ap.add_argument("--watch", type=float, default=300.0,
                    help="seconds to watch uplinks after ATZ")
    ap.add_argument("--host-mode", choices=["dns", "ip"], default="dns",
                    help="SERVADDR: proxy hostname (exercises QIDNSGIP) or fallback IP")
    args = ap.parse_args()

    du.load_dotenv(ROOT / ".env")
    pin = du.resolve_pin(device="ps-cb")
    if not pin:
        raise SystemExit("No PIN found. Set DRAGINO_PIN in .env")
    mqtt_cfg = load_config()
    if not mqtt_cfg.get("MQTT_PASS"):
        raise SystemExit("No MQTT_PASS. Create railway-mqtt.local.env first.")
    secrets.extend([pin, mqtt_cfg["MQTT_PASS"]])

    host = mqtt_cfg["MQTT_HOST"] if args.host_mode == "dns" else mqtt_cfg["MQTT_FALLBACK_IP"]
    addr = f"{host},{mqtt_cfg['MQTT_PORT']}"

    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = ROOT / "logs" / f"{ts}_openfw_m4_verify.log"
    logf = open(log_path, "w", buffering=1, encoding="utf-8", errors="replace")

    watch = BrokerWatch(mqtt_cfg)
    watch.start()
    out(f"broker subscriber: {'connected' if watch.connected.wait(15) else 'NOT CONNECTED'} "
        f"({mqtt_cfg['MQTT_HOST']}:{mqtt_cfg['MQTT_PORT']})")

    # No RESET prompt: the device is already running. We unlock the live
    # console, configure with per-command ACK checks, then ATZ for a clean
    # boot at a known time and watch the uplink cycles.
    ser = du.open_serial(args.port, 9600)
    buf = du.LineBuffer()

    out("=== logging in on the live console ===")
    if not unlock(ser, buf, pin):
        raise SystemExit(f"unlock failed; see {log_path}")
    out("unlock OK")

    desired = [
        ("DEBUG", "AT+DEBUG=1"),   # surface the raw QMTxxx URC result codes
        ("PRO", "AT+PRO=3,5"),
        ("SERVADDR", f"AT+SERVADDR={addr}"),
        ("CLIENT", f"AT+CLIENT={DEVICE_ID}"),
        ("UNAME", f"AT+UNAME={mqtt_cfg['MQTT_USER']}"),
        ("PWD", f"AT+PWD={mqtt_cfg['MQTT_PASS']}"),
        ("PUBTOPIC", f"AT+PUBTOPIC={PUB_TOPIC}"),
        ("SUBTOPIC", f"AT+SUBTOPIC={SUB_TOPIC}"),
        ("TLSMOD", "AT+TLSMOD=0,0"),
        ("MQOS", "AT+MQOS=1"),
        ("GDNS", "AT+GDNS=1"),
        ("BKDNS", f"AT+BKDNS=1,0,{mqtt_cfg['MQTT_FALLBACK_IP']},{mqtt_cfg['MQTT_PORT']}"),
        ("TDC", f"AT+TDC={args.tdc}"),
    ]
    out("=== applying MQTT parameters (ACK-checked) ===")
    failed = []
    for key, cmd in desired:
        if not send_acked(ser, buf, pin, cmd):
            failed.append(key)
    if failed:
        out(f"WARNING: not ACKed after retries: {', '.join(failed)}")

    out("=== AT+CFG dump (verification) ===")
    send_and_log(ser, buf, "AT+CFG", timeout=10.0)

    out("=== ATZ reboot; watching for uplinks ===")
    send_and_log(ser, buf, "ATZ", timeout=3.0)

    seen = {k: False for k in (
        "*****Upload start:", "Opened the MQTT client network successfully",
        "Successfully connected to the server", "Upload data successfully",
        "Failed to send", "*****End of upload*****", "Resolving domain name...",
        "Domain IP:", "MQTT parameter configuration error",
    )}
    deadline = time.monotonic() + args.watch
    while time.monotonic() < deadline:
        for line in du.read_for(ser, 1.0, buf, None):
            out(f"RX {line}")
            for key in seen:
                if key in line:
                    seen[key] = True
        if seen["Upload data successfully"] and watch.messages:
            # one full success on both sides; keep watching a little for a
            # second cycle, then stop early
            if sum(1 for _t, _b in watch.messages) >= 2:
                break

    ser.close()
    watch.stop()

    out("\n=== SUMMARY ===")
    for key, val in seen.items():
        out(f"  console '{key}': {val}")
    out(f"  broker messages: {len(watch.messages)}")
    json_ok = 0
    for topic, body in watch.messages:
        try:
            parsed = json.loads(body)
            json_ok += 1
            out(f"  valid JSON on {topic}: IMEI={parsed.get('IMEI')} "
                f"time={parsed.get('time')} signal={parsed.get('signal')}")
        except json.JSONDecodeError:
            out(f"  INVALID JSON on {topic}: {body[:120]}")
    out(f"log: {log_path}")
    logf.close()
    if not (seen["Upload data successfully"] and json_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
