#!/usr/bin/env python3
"""Minimal Railway restore on Password Correct."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
PIN = "358613"
PASS = "DrgN0-MqTt-7kR9wX2pL"
IP, PORT = "66.33.22.220", "33239"
for raw in (ROOT / "railway-mqtt.local.env").read_text(encoding="utf-8").splitlines():
    if raw.startswith("MQTT_PASS="):
        PASS = raw.split("=", 1)[1].strip()


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_restore_min.raw.log"
    ser = serial.Serial("COM8", 9600, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    buf = b""
    print(f"Opened COM8; log={logpath}", flush=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {s.replace(PASS, '***').replace(PIN, '***PIN***')}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(sec: float) -> list[str]:
        nonlocal buf
        end = time.time() + sec
        out: list[str] = []
        while time.time() < end:
            c = ser.read(4096)
            if c:
                buf += c
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    t = line.decode("utf-8", "replace").rstrip("\r")
                    if t:
                        log("RX", t)
                        out.append(t)
            else:
                time.sleep(0.02)
        return out

    def send(cmd: str, w: float = 0.7) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode())
        ser.flush()
        return read_lines(w)

    log("TEST", "wait Password Correct")
    end = time.time() + 150
    unlocked = False
    in_up = False
    while time.time() < end and not unlocked:
        lines = read_lines(0.8)
        if any("Upload start" in L for L in lines):
            in_up = True
        if any("End of upload" in L or "power-off" in L for L in lines):
            in_up = False
        if any("Password Correct" in L for L in lines):
            unlocked = True
            break
        if in_up:
            continue
        if any(
            L.strip() == "RDY" or "Signal Strength" in L or "Echo mode" in L for L in lines
        ):
            r = send(PIN, 1.6)
            if any("Password Correct" in x for x in r) or any(
                "Password Correct" in x for x in read_lines(0.8)
            ):
                unlocked = True
                break
    if not unlocked:
        log("TEST", "FAIL unlock")
        ser.close()
        raise SystemExit(2)

    log("TEST", "RESTORE burst")
    for cmd, w in [
        (f"AT+SERVADDR={IP},{PORT}", 0.7),
        (f"AT+BKDNS=1,0,{IP},{PORT}", 0.7),
        ("AT+UNAME=dragino", 0.5),
        (f"AT+PWD={PASS}", 0.5),
        ("AT+PRO=3,5", 0.5),
        ("AT+TLSMOD=0,0", 0.5),
        ("AT+CLIENT=ltc2", 0.5),
        ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
        ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
        (f"AT+SERVADDR={IP},{PORT}", 0.7),
        (f"AT+BKDNS=1,0,{IP},{PORT}", 0.7),
    ]:
        send(cmd, w)
    for q in (
        "AT+SERVADDR=?",
        "AT+BKDNS=?",
        "AT+UNAME=?",
        "AT+CLIENT=?",
        "AT+PUBTOPIC=?",
        "AT+PRO=?",
    ):
        send(q, 0.9)
    log("TEST", "DONE")
    ser.close()


if __name__ == "__main__":
    main()
