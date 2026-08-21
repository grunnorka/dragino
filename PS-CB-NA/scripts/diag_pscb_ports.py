#!/usr/bin/env python3
"""Find out whether the modem completes an MQTT session, using IP addresses only.

With APN unset the device has working data (DNS and NTP both succeed). Earlier
runs sometimes printed 'Failed to open the MQTT client network' on Railway's
high port, but a later cycle opened TCP to the same IP:port successfully and
still never printed 'Successfully connected to the server'. So this harness
compares destinations to isolate handshake / broker behaviour — not "blocked
port" by itself:

  phase A  66.33.22.220:33239   Railway TCP proxy, authenticated
  phase B  54.36.178.49:1883    public broker, standard port, anonymous
  phase C  restore Railway

No hostnames are ever written to the device, so DNS is never involved.
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
from at_session import at_cmd, read_cfg  # noqa: E402
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

MARKERS = {
    "pdp_fail": "Failed to activate PDP context",
    "pdp_ok": "Successfully activated PDP context",
    "dns_ok": "DNS configuration is successful",
    "dns_fail": "DNS configuration failed",
    "time_fail": "Failed to get time",
    "open_fail": "Failed to open the MQTT client network",
    "mqtt_cfg_fail": "MQTT configuration failed",
    "param_error": "parameter configuration error",
    "connected": "Successfully connected to the server",
    "upload_ok": "Upload data successfully",
    "send_fail": "Failed to send",
}


class BrokerWatch:
    def __init__(self, cfg: dict[str, str]) -> None:
        self.cfg = cfg
        self.messages: list[tuple[str, str, str]] = []
        self.connected = threading.Event()
        self._client = None

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client_id = f"pscb-diag-{os.getpid()}"
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
    parser.add_argument("--alt-ip", default="54.36.178.49")
    parser.add_argument("--alt-port", type=int, default=1883)
    parser.add_argument("--boot-wait", type=float, default=200.0)
    parser.add_argument(
        "--skip-alt", action="store_true", help="only restore APN and retest Railway"
    )
    args = parser.parse_args()

    pin = resolve_pin()
    if not pin:
        raise SystemExit("No PIN found. Set DRAGINO_PIN in .env")
    mqtt_cfg = load_config()
    rail_ip = mqtt_cfg["MQTT_FALLBACK_IP"]
    rail_port = mqtt_cfg["MQTT_PORT"]
    secrets = [pin, mqtt_cfg["MQTT_PASS"]]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logpath = logdir / f"{stamp}_pscb_port_diag.log"
    log = logpath.open("w", encoding="utf-8")

    def out(text: str) -> None:
        safe = text
        for secret in secrets:
            if secret:
                safe = safe.replace(secret, "***")
        print(safe, flush=True)
        log.write(safe + "\n")
        log.flush()

    watch = BrokerWatch(mqtt_cfg)
    watch.start()
    out(
        f"Railway subscriber: "
        f"{'connected' if watch.connected.wait(15) else 'NOT CONNECTED'}"
    )

    prompt_user.step(
        "Reboot the sensor to start the diagnostic",
        [
            "SW1 must be in the Flash (normal) position.",
            "",
            "1. Click the button below.",
            "2. Then press RESET on the board.",
            "",
            "This runs 2 to 3 reboot cycles and takes roughly 12 minutes.",
            "No further action is needed from you.",
        ],
        ok_label="Ready - I will press RESET now",
    )

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()

    def login(timeout: float = 200.0) -> bool:
        result = unlock(ser, pin, policy="stable", timeout=timeout, on_line=out, on_tx=out)
        out(f"  unlock ok={result.ok} phase={result.phase.value}")
        return result.ok

    def apply(commands: list[str]) -> None:
        for cmd in commands:
            acked, payload = at_cmd(ser, cmd, buf, out, timeout=12.0)
            shown = cmd
            for secret in secrets:
                if secret:
                    shown = shown.replace(secret, "***")
            out(f"  {shown} -> {'ACK' if acked else 'NO ACK'} {' '.join(payload)}".rstrip())

    def observe(label: str) -> dict[str, bool]:
        out(f"  ATZ and watching up to {args.boot_wait:.0f}s ...")
        at_cmd(ser, "ATZ", buf, out, timeout=8.0)
        seen = {key: False for key in MARKERS}
        deadline = time.time() + args.boot_wait
        while time.time() < deadline:
            for line in read_for(ser, 1.0, buf, out):
                for key, needle in MARKERS.items():
                    if needle in line:
                        seen[key] = True
            if seen["upload_ok"]:
                read_for(ser, 12.0, buf, out)
                break
            if seen["send_fail"] and (seen["open_fail"] or seen["mqtt_cfg_fail"]):
                # Failure already proven for this phase; stop early.
                read_for(ser, 4.0, buf, out)
                break
        out(f"  [{label}] " + ", ".join(k for k, v in seen.items() if v))
        return seen

    results: dict[str, dict[str, bool]] = {}

    out("\n########## phase A: APN=NULL + Railway IP ##########")
    if not login():
        ser.close()
        watch.stop()
        log.close()
        raise SystemExit(f"Could not unlock; see {logpath}")
    apply(
        [
            "AT+APN=NULL",
            f"AT+SERVADDR={rail_ip},{rail_port}",
            f"AT+BKDNS=1,0,{rail_ip},{rail_port}",
            f"AT+UNAME={mqtt_cfg['MQTT_USER']}",
            f"AT+PWD={mqtt_cfg['MQTT_PASS']}",
            f"AT+CLIENT={DEVICE_ID}",
            f"AT+PUBTOPIC={PUB_TOPIC}",
            f"AT+SUBTOPIC={SUB_TOPIC}",
        ]
    )
    cfg_a = read_cfg(ser, buf, out)
    out(f"  APN now = {cfg_a.get('APN')!r} (want 'NULL')")
    out(f"  SERVADDR now = {cfg_a.get('SERVADDR')!r}")
    results["A_railway_33239"] = observe("A railway :33239")

    if not args.skip_alt and not results["A_railway_33239"]["upload_ok"]:
        out(f"\n########## phase B: {args.alt_ip}:{args.alt_port} anonymous ##########")
        if login():
            apply(
                [
                    f"AT+SERVADDR={args.alt_ip},{args.alt_port}",
                    f"AT+BKDNS=1,0,{args.alt_ip},{args.alt_port}",
                    "AT+UNAME=",
                    "AT+PWD=",
                ]
            )
            cfg_b = read_cfg(ser, buf, out)
            out(f"  SERVADDR now = {cfg_b.get('SERVADDR')!r}")
            results["B_public_1883"] = observe(f"B public :{args.alt_port}")
        else:
            out("  could not log in for phase B")

    out("\n########## phase C: restore Railway ##########")
    if login():
        apply(
            [
                f"AT+SERVADDR={rail_ip},{rail_port}",
                f"AT+BKDNS=1,0,{rail_ip},{rail_port}",
                f"AT+UNAME={mqtt_cfg['MQTT_USER']}",
                f"AT+PWD={mqtt_cfg['MQTT_PASS']}",
                f"AT+CLIENT={DEVICE_ID}",
                f"AT+PUBTOPIC={PUB_TOPIC}",
                f"AT+SUBTOPIC={SUB_TOPIC}",
                "AT+APN=NULL",
            ]
        )
        cfg_c = read_cfg(ser, buf, out)
        for key in ("PRO", "SERVADDR", "CLIENT", "UNAME", "PUBTOPIC", "APN", "TDC"):
            out(f"  {key} = {cfg_c.get(key)!r}")
    else:
        out("  could not log in to restore; re-run fix_pscb_mqtt.py")

    ser.close()
    time.sleep(2)
    watch.stop()

    out("\n########## RESULT ##########")
    for phase, seen in results.items():
        hits = ", ".join(k for k, v in seen.items() if v) or "nothing"
        out(f"  {phase}: {hits}")
    out(f"  Railway broker messages received: {len(watch.messages)}")
    for stamp_, topic, body in watch.messages:
        out(f"    {stamp_} {topic} {body}")

    a = results.get("A_railway_33239", {})
    b = results.get("B_public_1883", {})
    out("\n  interpretation:")
    if a.get("upload_ok"):
        out("    Railway uplink works; nothing more to do.")
    elif a.get("connected") or (
        # TCP open without CONNACK was seen on this unit; treat as handshake fail.
        not a.get("net_open_fail") and a.get("send_fail")
    ):
        out(
            "    Railway TCP path can open; failure is MQTT CONNECT/CONNACK\n"
            "    (or publish), not a blocked port. Check broker logs and try MQOS=0."
        )
    elif b.get("connected") or b.get("upload_ok"):
        out(
            "    Handshake works on the alternative broker. Compare that path to\n"
            "    Railway (auth, QoS, proxy latency) — do not assume port blocking;\n"
            "    this unit has opened TCP to :33239 before."
        )
    elif b:
        out(
            "    Neither destination completed a usable MQTT session. Dig into\n"
            "    modem MQTT client / broker compatibility, not just port numbers."
        )
    else:
        out("    Phase B did not run; inconclusive.")
    out(f"\n  log: {logpath}")
    log.close()


if __name__ == "__main__":
    main()
