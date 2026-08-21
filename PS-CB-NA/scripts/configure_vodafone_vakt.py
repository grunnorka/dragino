#!/usr/bin/env python3
"""Configure a PS-CB-NA with Vodafone SIM for vakt.systemat.is (ThingsBoard).

Uses the idle-unlock pattern to survive the device sleeping between uploads.
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

PORT = "/dev/ttyUSB2"
BAUD = 9600
SERVADDR = "167.235.104.181,1883"
BKDNS = "1,0,167.235.104.181,1883"
PUB = "v1/devices/me/telemetry"
SUB = "v1/devices/me/attributes"
CLIENT = "null"
MQOS = "1"
TLSMOD = "0,0"
TDC = "180"  # test interval; increase after verification


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("TB_TOKEN"))
    parser.add_argument("--port", default=PORT)
    args = parser.parse_args()

    if not args.token:
        print("ERROR: Set TB_TOKEN or pass --token", file=sys.stderr)
        return 2

    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

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

            print("Reading IMSI to choose APN...", flush=True)
            imsi = ""
            for line in send("AT+IMSI=?", 2.5) or []:
                stripped = line.strip()
                if stripped and not stripped.startswith("AT") and not stripped.startswith("[") and stripped != "OK":
                    imsi = stripped
            log("SYS", f"IMSI={imsi}")

            if imsi.startswith("90128"):
                apn = "NULL"  # Vodafone GDSP: network-supplied APN
            elif imsi.startswith("27402"):
                apn = "lpwa.vodafone.is"  # Vodafone Iceland / Syn
            else:
                apn = "lpwa.vodafone.is"  # safe default for Vodafone
            log("SYS", f"APN={apn}")

            print("Applying Vodafone + vakt.systemat.is config...", flush=True)
            for cmd in [
                "AT+PRO=3,5",
                f"AT+SERVADDR={SERVADDR}",
                f"AT+UNAME={args.token}",
                "AT+PWD=NULL",
                f"AT+PUBTOPIC={PUB}",
                f"AT+SUBTOPIC={SUB}",
                f"AT+CLIENT={CLIENT}",
                f"AT+MQOS={MQOS}",
                f"AT+TLSMOD={TLSMOD}",
                f"AT+BKDNS={BKDNS}",
                f"AT+APN={apn}",
                f"AT+TDC={TDC}",
            ]:
                send(cmd, 2.0)

            print("Verifying...", flush=True)
            for cmd in ["AT+CFG", "AT+PRO=?", "AT+SERVADDR=?", "AT+APN=?", "AT+TDC=?", "AT+BKDNS=?"]:
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
