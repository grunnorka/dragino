#!/usr/bin/env python3
"""Partial factory reset (AT+FDR1) and reconfigure Vodafone PS-CB-NA for vakt.systemat.is.

AT+FDR1 resets:
  - PRO=2,0 (UDP)
  - TDC=7200 (factory default, 2 hours)
  - MQOS=0
  - BKDNS=1,0,NULL

It does NOT reset:
  - SERVADDR, CLIENT, UNAME, PWD, PUBTOPIC, SUBTOPIC, APN, IOTMOD, PWORD

After FDR1 we re-apply only what was reset, leaving TDC at 7200.
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
SERVADDR = "167.235.104.181,1883"
BKDNS = "1,0,167.235.104.181,1883"
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

    token = os.environ.get("TB_TOKEN", "")
    if not token:
        print("ERROR: TB_TOKEN env var not set", file=sys.stderr)
        return 2

    print(f"Port={args.port} FDR1 + reconfigure for vakt.systemat.is", flush=True)
    ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***").replace(token, "***TOKEN***")
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
        shown = shown.replace(token, "***TOKEN***")
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

    def reconfigure() -> None:
        print("Re-applying settings after FDR1...", flush=True)
        for cmd in [
            f"AT+PRO=3,5",
            f"AT+MQOS=1",
            f"AT+BKDNS={BKDNS}",
            f"AT+CLOCKLOG={CLOCKLOG}",
            # Re-assert the token and topics in case FDR1 clears them on some firmware builds.
            f"AT+UNAME={token}",
            f"AT+PUBTOPIC=v1/devices/me/telemetry",
            f"AT+SUBTOPIC=v1/devices/me/attributes",
        ]:
            send(cmd, 2.0)

        print("Verifying...", flush=True)
        for cmd in ["AT+CFG", "AT+PRO=?", "AT+SERVADDR=?", "AT+BKDNS=?", "AT+APN=?", "AT+TDC=?", "AT+CLOCKLOG=?"]:
            send(cmd, 2.5)

        print("Rebooting with ATZ...", flush=True)
        send("ATZ", 2.0)

    for attempt in range(10):
        try:
            if not ser.is_open:
                ser.open()

            print(f"Attempt {attempt + 1}: waiting for idle before FDR1...", flush=True)
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

            print("Sending AT+FDR1 (partial reset, will reboot immediately)...", flush=True)
            send("AT+FDR1", 2.0)
            # FDR1 reboots immediately; no OK expected.
            try:
                ser.close()
            except Exception:
                pass

            print("Waiting for device to reboot...", flush=True)
            time.sleep(15.0)

            print("Reopening serial port...", flush=True)
            ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
            try:
                ser.dtr = False
                ser.rts = False
            except Exception:
                pass
            buf = bytearray()

            print("Waiting for post-FDR1 idle...", flush=True)
            if not wait_for_idle(180.0):
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                continue

            print("Unlocking after FDR1...", flush=True)
            if not unlock():
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                continue

            reconfigure()
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
