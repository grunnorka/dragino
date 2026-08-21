#!/usr/bin/env python3
"""Factory-reset the PS-CB-NA settings and re-apply the Railway MQTT config.

Uses AT+FDR1 (factory defaults *except* passwords) so the AT PIN survives, then
writes the full parameter set from SETUP.md section 5 in the documented order
(PRO -> SERVADDR -> auth/topics -> BKDNS -> TDC -> APN), reboots with ATZ,
re-checks persistence, and watches the first upload on serial and on the broker.

Every value comes from railway-mqtt.local.env / .env; nothing is hard-coded.
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

REPORT_KEYS = [
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
    "IOTMOD",
]

MARKERS = {
    "signal_none": "Signal Strength:99",
    "pdp_fail": "Failed to activate PDP context",
    "pdp_ok": "Successfully activated PDP context",
    "dns_ok": "DNS configuration is successful",
    "dns_fail": "DNS configuration failed",
    "time_fail": "Failed to get time",
    "net_open_fail": "Failed to open the MQTT client network",
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

        client_id = f"pscb-reset-{os.getpid()}"
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
        help="SERVADDR from the Railway fallback IP or the proxy hostname",
    )
    parser.add_argument(
        "--reset-cmd",
        choices=["FDR1", "FDR", "none"],
        default="FDR1",
        help="FDR1 keeps the AT password; FDR wipes it too; none only re-applies",
    )
    parser.add_argument("--boot-wait", type=float, default=280.0)
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
    addr = f"{host},{mqtt_cfg['MQTT_PORT']}"
    secrets = [pin, mqtt_cfg["MQTT_PASS"]]

    # key -> (value as AT+CFG reports it, command that sets it). Order matters:
    # PRO first because it can rewrite server defaults, APN last.
    desired: dict[str, tuple[str, str]] = {
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
        # The Vodafone GDSP SIM gets its APN from the network. NULL is the unset
        # state; AT+APN= (empty) is a different, worse setting.
        "APN": ("NULL", "AT+APN=NULL"),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logpath = logdir / f"{stamp}_pscb_reset_reapply.log"
    log = logpath.open("w", encoding="utf-8")

    def out(text: str) -> None:
        safe = text
        for secret in secrets:
            if secret:
                safe = safe.replace(secret, "***")
        print(safe, flush=True)
        log.write(safe + "\n")
        log.flush()

    def report(cfg: dict[str, str], title: str) -> list[str]:
        out(f"\n--- {title} ---")
        wrong: list[str] = []
        for key in REPORT_KEYS:
            value = cfg.get(key)
            shown = "<absent>" if value is None else (value or "<empty>")
            if key == "PWD" and value and not is_unset(value):
                shown = "***"
            flag = ""
            if key in desired:
                want = desired[key][0]
                ok = (value or "").strip() == want
                flag = " OK" if ok else (" WANT ***" if key == "PWD" else f" WANT {want}")
                if not ok:
                    wrong.append(key)
            out(f"  {key:9} = {shown}{flag}")
        return wrong

    watch = BrokerWatch(mqtt_cfg)
    watch.start()
    out(
        f"broker subscriber: "
        f"{'connected' if watch.connected.wait(15) else 'NOT CONNECTED'} "
        f"({mqtt_cfg['MQTT_HOST']}:{mqtt_cfg['MQTT_PORT']})"
    )
    out(f"reset command: {args.reset_cmd}   SERVADDR target: {addr}   TDC: {args.tdc}")

    prompt_user.step(
        "Reboot the sensor to start the reset",
        [
            "SW1 must be in the Flash (normal) position.",
            "",
            "1. Click the button below.",
            "2. Then press RESET on the board.",
            "",
            f"I will back up the settings, run AT+{args.reset_cmd} and write the",
            "Railway MQTT config again. Expect about 12 minutes with no",
            "further action needed from you.",
        ],
        ok_label="Ready - I will press RESET now",
    )

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()

    def login(timeout: float = 220.0) -> bool:
        result = unlock(ser, pin, policy="stable", timeout=timeout, on_line=out, on_tx=out)
        out(f"  unlock ok={result.ok} phase={result.phase.value} hint={result.hint}")
        return result.ok

    def write_keys(keys: list[str]) -> list[str]:
        failed: list[str] = []
        for key in keys:
            command = desired[key][1]
            shown = command
            for secret in secrets:
                if secret:
                    shown = shown.replace(secret, "***")
            acked, payload = at_cmd(ser, command, buf, out, timeout=12.0)
            note = " ".join(payload)
            out(f"  {shown} -> {'ACK' if acked else 'NO ACK'} {note}".rstrip())
            if not acked:
                failed.append(key)
        return failed

    def observe(label: str) -> dict[str, bool]:
        seen = {key: False for key in MARKERS}
        deadline = time.time() + args.boot_wait
        while time.time() < deadline:
            for line in read_for(ser, 1.0, buf, out):
                for key, needle in MARKERS.items():
                    if needle in line:
                        seen[key] = True
            if seen["upload_ok"]:
                read_for(ser, 15.0, buf, out)
                break
        hits = ", ".join(k for k, v in seen.items() if v) or "nothing"
        out(f"  [{label}] {hits}")
        return seen

    out("\n########## 1. log in and back up the current settings ##########")
    if not login():
        ser.close()
        watch.stop()
        log.close()
        raise SystemExit(f"Could not unlock; see {logpath}")
    before = read_cfg(ser, buf, out)
    out(f"parsed {len(before)} settings from AT+CFG (full dump is in the log)")
    report(before, "settings BEFORE the reset")

    if args.reset_cmd != "none":
        out(f"\n########## 2. AT+{args.reset_cmd} factory reset ##########")
        acked, payload = at_cmd(ser, f"AT+{args.reset_cmd}", buf, out, timeout=20.0)
        out(f"  AT+{args.reset_cmd} -> {'ACK' if acked else 'NO ACK'} {' '.join(payload)}".rstrip())
        out("  waiting for the device to come back, then logging in again")
        read_for(ser, 8.0, buf, out)
        if not login():
            ser.close()
            watch.stop()
            log.close()
            raise SystemExit(f"Could not unlock after the reset; see {logpath}")
        factory = read_cfg(ser, buf, out)
        report(factory, "factory defaults after the reset")
    else:
        out("\n########## 2. reset skipped (--reset-cmd none) ##########")

    out("\n########## 3. writing the full Railway MQTT config ##########")
    no_ack = write_keys(list(desired))
    if no_ack:
        out(f"  WARNING: no acknowledgement for: {', '.join(no_ack)}")
    wrong = report(read_cfg(ser, buf, out), "settings AFTER writing (before reboot)")
    if wrong:
        out(f"  re-writing fields that did not stick: {', '.join(wrong)}")
        write_keys(wrong)
        wrong = report(read_cfg(ser, buf, out), "settings after the second write")

    out("\n########## 4. ATZ so AT+PRO takes effect, then watch the uplink ##########")
    at_cmd(ser, "ATZ", buf, out, timeout=8.0)
    out(f"  watching boot and first upload for up to {args.boot_wait:.0f}s")
    seen = observe("after ATZ")

    out("\n########## 5. persistence check ##########")
    drift: list[str] = []
    if login(200.0):
        drift = report(read_cfg(ser, buf, out), "settings AFTER reboot")
        if drift:
            out(f"  fields that did NOT survive the reboot: {', '.join(drift)}")
            write_keys(drift)
            drift = report(read_cfg(ser, buf, out), "settings after repairing the drift")
    else:
        out("  could not log in again to confirm persistence")

    ser.close()
    time.sleep(2)
    watch.stop()

    out("\n########## SUMMARY ##########")
    out(f"  reset command run: {args.reset_cmd}")
    out(f"  fields wrong before reboot: {', '.join(wrong) if wrong else 'none'}")
    out(f"  fields wrong after reboot:  {', '.join(drift) if drift else 'none'}")
    for key in ("signal_none", "pdp_ok", "dns_ok", "net_open_fail", "mqtt_cfg_fail",
                "param_error", "connected", "upload_ok"):
        out(f"  serial {MARKERS[key]!r}: {seen.get(key, False)}")
    out(f"  broker messages received: {len(watch.messages)}")
    for stamp_, topic, body in watch.messages:
        out(f"    {stamp_} {topic} {body}")
    out(f"  log: {logpath}")
    log.close()
    if not (seen.get("upload_ok") and watch.messages):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
