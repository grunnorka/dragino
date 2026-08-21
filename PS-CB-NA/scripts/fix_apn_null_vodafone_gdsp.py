#!/usr/bin/env python3
"""Set APN=NULL for a Vodafone GDSP SIM on PS-CB-NA and reboot."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
from monitor import load_dotenv, resolve_pin

PORT = "/dev/ttyUSB2"
BAUD = 9600


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    ser = serial.Serial(PORT, BAUD, timeout=0.25, write_timeout=2)
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
            quiet: float | None = None
            end = time.monotonic() + 120.0
            while time.monotonic() < end:
                lines = drain(1.0)
                if lines is None:
                    break
                for line in lines:
                    if "Upload start" in line or "Searching for location" in line:
                        quiet = None
                    if "End of upload" in line or "power-off successful" in line:
                        quiet = time.monotonic()
                if quiet and time.monotonic() - quiet > 3:
                    break
                if not lines and quiet is None:
                    if not drain(3.0):
                        break

            if not unlock():
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                continue

            print("Setting APN=NULL for Vodafone GDSP...", flush=True)
            for cmd in ["AT+APN=NULL", "AT+CFG", "AT+APN=?"]:
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
