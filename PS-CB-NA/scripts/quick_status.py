#!/usr/bin/env python3
"""Quick status check: unlock and dump AT+CFG."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
from monitor import load_dotenv, resolve_pin

BAUD = 9600


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    print(f"Port={args.port} quick status check", flush=True)
    try:
        ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
    except serial.SerialException as exc:
        print(f"Cannot open port: {exc}", file=sys.stderr)
        return 2
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***")
        print(f"{utc()} {tag} {safe}", flush=True)

    def drain(seconds: float) -> list[str]:
        end = time.monotonic() + seconds
        out: list[str] = []
        while time.monotonic() < end:
            try:
                chunk = ser.read(4096)
            except serial.SerialException:
                break
            if chunk:
                buf.extend(chunk)
                while True:
                    i = buf.find(b"\n")
                    if i < 0:
                        break
                    t = bytes(buf[: i + 1]).decode("utf-8", errors="replace").rstrip("\r\n")
                    del buf[: i + 1]
                    if t:
                        log("RX", t)
                        out.append(t)
            else:
                time.sleep(0.02)
        return out

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        log("TX", shown)
        ser.write((cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
        ser.flush()
        return drain(wait)

    unlocked = False
    for i in range(1, 30):
        got = send(pin, 2.0)
        if any("Password Correct" in g for g in got):
            unlocked = True
            log("SYS", f"unlock_ok {i}")
            break
        send("AT", 1.0)

    if not unlocked:
        print("Could not unlock (device may be sleeping).", flush=True)
        ser.close()
        return 1

    for cmd in ["AT+CFG", "AT+TDC=?", "AT+CLOCKLOG=?", "AT+SERVADDR=?", "AT+PRO=?", "AT+APN=?", "AT+BKDNS=?"]:
        send(cmd, 2.5)

    ser.close()
    print("Done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
