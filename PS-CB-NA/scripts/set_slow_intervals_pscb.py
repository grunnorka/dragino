#!/usr/bin/env python3
"""Set the slowest intervals supported by this PS-CB-NA firmware.

Firmware v1.2.1 observations:
  - AT+TDC above 7200 s does not stick; 7200 s (2 h) is the maximum usable value.
  - AT+CLOCKLOG interval above 255 min wraps / is rejected; use 240 min (4 h).

Result: upload every 2 h, sample every 4 h, keep 6 history slots (24 h coverage).
"""

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
TDC = 1800
CLOCKLOG = "1,65535,240,6"


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

    print(f"Port={args.port} TDC={TDC}s CLOCKLOG={CLOCKLOG}", flush=True)
    ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***")
        print(f"{utc()} {tag} {safe}", flush=True)

    def drain(seconds: float) -> list[str] | None:
        end = time.monotonic() + seconds
        out: list[str] = []
        while time.monotonic() < end:
            try:
                chunk = ser.read(4096)
            except serial.SerialException:
                return None
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

    def send(cmd: str, wait: float = 2.0) -> list[str] | None:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        log("TX", shown)
        ser.write((cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
        ser.flush()
        return drain(wait)

    def wait_for_idle(timeout: float = 120.0) -> bool:
        quiet: float | None = None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            lines = drain(1.0)
            if lines is None:
                return False
            for line in lines:
                if "Upload start" in line or "Searching for location" in line:
                    quiet = None
                if "End of upload" in line or "power-off successful" in line:
                    quiet = time.monotonic()
            if quiet and time.monotonic() - quiet > 3:
                return True
            if not lines and quiet is None:
                if not drain(3.0):
                    return True
        return False

    def unlock() -> bool:
        for i in range(1, 60):
            got = send(pin, 2.0)
            if got is None:
                return False
            if any("Password Correct" in g for g in got):
                log("SYS", f"unlock_ok {i}")
                return True
            send(f"AT+PIN={pin}", 1.0)
            got = send("AT", 1.2)
            if got is None:
                return False
            if any(g.strip().upper() == "OK" for g in got):
                if any("OK" in g for g in send("AT", 1.0)):
                    log("SYS", f"AT_OK {i}")
                    return True
        return False

    for attempt in range(10):
        try:
            if not ser.is_open:
                ser.open()
            print(f"Attempt {attempt + 1}: waiting for idle...", flush=True)
            if not wait_for_idle(120.0):
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                continue
            print("Idle. Unlocking...", flush=True)
            if not unlock():
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                continue

            print("Waiting for network attach (+CCLK / NBIOT has responded / Signal Strength)...", flush=True)
            attached = False
            attach_deadline = time.monotonic() + 120.0
            while time.monotonic() < attach_deadline:
                lines = drain(1.0)
                if lines is None:
                    attached = False
                    break
                for line in lines:
                    if any(marker in line for marker in ("+CCLK:", "NBIOT has responded", "Signal Strength")):
                        attached = True
                        print(f"Network attach marker: {line}", flush=True)
                if attached:
                    time.sleep(2.0)
                    break

            print("Setting slow intervals...", flush=True)
            for cmd in [f"AT+CLOCKLOG={CLOCKLOG}", f"AT+TDC={TDC}"]:
                send(cmd, 2.0)
            # Give the firmware a moment to commit, then verify immediately.
            time.sleep(1.0)
            send(f"AT+TDC=?", 2.0)

            print("Verifying...", flush=True)
            for cmd in ["AT+CFG", "AT+CLOCKLOG=?", "AT+TDC=?"]:
                send(cmd, 2.5)

            print("Rebooting with ATZ...", flush=True)
            send("ATZ", 2.0)
            ser.close()
            print("Done.", flush=True)
            return 0
        except serial.SerialException as exc:
            print(f"Serial error: {exc}, retrying...", flush=True)
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(1.0)

    print("Failed after retries", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
