#!/usr/bin/env python3
"""Quiet-window force restore LTC2 to Railway altaria:33239."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
PIN = "358613"
COM, BAUD = "COM8", 9600
IP, PORT = "66.33.22.220", "33239"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
PASS = "DrgN0-MqTt-7kR9wX2pL"
envp = ROOT / "railway-mqtt.local.env"
if envp.is_file():
    for raw in envp.read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            PASS = raw.split("=", 1)[1].strip()
        if raw.startswith("MQTT_FALLBACK_IP="):
            IP = raw.split("=", 1)[1].strip()


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_force_restore.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    print(f"Opened {COM}; restore {IP},{PORT}; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(PASS, "***").replace(PIN, "***PIN***")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(seconds: float) -> list[str]:
        nonlocal buf
        end = time.time() + seconds
        out: list[str] = []
        while time.time() < end:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    if text:
                        log("RX", text)
                        out.append(text)
            else:
                time.sleep(0.02)
        return out

    def send(cmd: str, wait: float = 0.7) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def restore() -> dict[str, str]:
        send(PIN, 0.4)
        for _ in range(3):
            send(f"AT+SERVADDR={IP},{PORT}", 0.55)
            send(PIN, 0.12)
            send(f"AT+BKDNS=1,0,{IP},{PORT}", 0.55)
            send(PIN, 0.12)
        for cmd in (
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={PASS}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            "AT+TDC=60",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
        ):
            send(PIN, 0.12)
            send(cmd, 0.5)
        cfg = {}
        for key, q in [
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("UNAME", "AT+UNAME=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("PRO", "AT+PRO=?"),
        ]:
            send(PIN, 0.12)
            for L in send(q, 0.8):
                t = L.strip()
                if t and t != "OK" and not t.startswith("[") and "Password" not in t:
                    cfg[key] = t
        log("CFG", str(cfg))
        return cfg

    end = time.time() + 180
    in_upload = False
    done = False
    while time.time() < end and not done:
        for L in read_lines(1.0):
            if "Upload start" in L:
                in_upload = True
            if "End of upload" in L or "NB module power-off" in L:
                in_upload = False
            if in_upload:
                continue
            if L.strip() == "RDY" or "Signal Strength" in L or "Echo mode turned off" in L or "Password timeout" in L:
                cfg = restore()
                ok = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")
                log("TEST", f"RESTORE ok={ok} cfg={cfg}")
                if ok:
                    done = True
                    break
    print("=== SUMMARY ===", done, flush=True)
    ser.close()
    raise SystemExit(0 if done else 1)


if __name__ == "__main__":
    main()
