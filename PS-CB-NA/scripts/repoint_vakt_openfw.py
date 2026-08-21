#!/usr/bin/env python3
"""Repoint an already-configured PS-CB-NA at vakt.systemat.is (ThingsBoard).

Unlike configure_vodafone_vakt.py this does NOT run AT+FDR1 and does NOT touch
APN/TDC/PRO -- it only swaps broker address, credentials, and topics. Use when
the unit is already attached and only the MQTT target is wrong (for example a
unit pointed at the Railway proxy IP, which the GDSP IDER_ACL blocks).

Set TB_TOKEN in the environment before running.
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
PUB = "v1/devices/me/telemetry"
SUB = "v1/devices/me/attributes"
CLIENT = "null"
MQOS = "1"
TLSMOD = "0,0"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("TB_TOKEN"))
    parser.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB2"))
    args = parser.parse_args()

    if not args.token:
        print("ERROR: Set TB_TOKEN or pass --token", file=sys.stderr)
        return 2

    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    print(f"Port={args.port} repoint to vakt.systemat.is (no FDR1, APN/TDC untouched)", flush=True)
    ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***").replace(args.token, "***TOKEN***")
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
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd.replace(args.token, "***TOKEN***")
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

            print("Applying vakt.systemat.is broker config...", flush=True)
            for cmd in [
                f"AT+SERVADDR={SERVADDR}",
                f"AT+UNAME={args.token}",
                "AT+PWD=NULL",
                f"AT+PUBTOPIC={PUB}",
                f"AT+SUBTOPIC={SUB}",
                f"AT+CLIENT={CLIENT}",
                f"AT+MQOS={MQOS}",
                f"AT+TLSMOD={TLSMOD}",
                f"AT+BKDNS={BKDNS}",
            ]:
                send(cmd, 2.0)

            print("Verifying...", flush=True)
            for cmd in [
                "AT+SERVADDR=?",
                "AT+BKDNS=?",
                "AT+UNAME=?",
                "AT+PUBTOPIC=?",
                "AT+PRO=?",
                "AT+MQOS=?",
                "AT+APN=?",
                "AT+TDC=?",
            ]:
                send(cmd, 2.5)

            print("Rebooting with ATZ to apply...", flush=True)
            send("ATZ", 2.0)
            ser.close()
            print("Done. Next uplink cycle should target vakt.systemat.is.", flush=True)
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
