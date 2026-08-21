#!/usr/bin/env python3
"""Set APN/TDC on a PS-CB-NA, reboot with ATZ, and capture the next upload cycle.

Debug helper for the vakt.systemat.is MQTT failure (2026-08-18): the GDSP SIM
sits behind an operator ACL on APN lpwa.vodafone.io that only allows
167.235.104.181 (see docs/VODAFONE_CONNECTIVITY.md). Use --apn to switch the APN
and --tdc for a short burst interval, then watch whether QMTOPEN succeeds on
the next boot cycle.

Secrets: PIN comes from .env; any TB_TOKEN found in forQwen.md is used purely
as a redaction filter. Neither is ever printed or written to logs.
"""

from __future__ import annotations

import argparse
import os
import re
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
    parser.add_argument("--apn", help="APN to set (e.g. lpwa.vodafone.io or NULL); omit to leave unchanged")
    parser.add_argument("--tdc", type=int, help="TDC seconds to set; omit to leave unchanged")
    parser.add_argument("--capture", type=float, default=300.0, help="seconds to capture after ATZ")
    parser.add_argument("--no-reboot", action="store_true", help="apply settings without ATZ")
    parser.add_argument("--debug", action="store_true", help="enable AT+DEBUG=1 (raw modem RX dump)")
    parser.add_argument("--dnscfg", help='DNS server(s) to set, e.g. "1.1.1.1" (ACL-whitelisted)')
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    secrets = [pin]
    handoff = ROOT / "forQwen.md"
    if handoff.exists():
        secrets += re.findall(r"TB_TOKEN=([A-Za-z0-9]+)", handoff.read_text())

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = ROOT / "logs" / f"{ts}_burst_debug.raw.log"

    print(f"Port={args.port} APN={args.apn or '-'} TDC={args.tdc or '-'} capture={args.capture:.0f}s", flush=True)
    print(f"Raw log: {log_path}", flush=True)

    logf = open(log_path, "a", encoding="utf-8")
    ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()

    def redact(text: str) -> str:
        for s in secrets:
            if s:
                text = text.replace(s, "***")
        return text

    def log(tag: str, text: str) -> None:
        safe = redact(text)
        line = f"{utc()} {tag} {safe}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

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
        shown = redact(shown)
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

    try:
        print("Waiting for idle...", flush=True)
        wait_for_idle(120.0)

        print("Unlocking...", flush=True)
        if not unlock():
            print("Could not unlock (device may be sleeping; press ACT 1-3 s and retry).", flush=True)
            return 1

        print("Diagnostic dump...", flush=True)
        for cmd in [
            "AT+CFG",
            "AT+TDC=?",
            "AT+APN=?",
            "AT+SERVADDR=?",
            "AT+BKDNS=?",
            "AT+DNSCFG=?",
            "AT+GDNS=?",
            "AT+CLOCKLOG=?",
            "AT+IOTMOD=?",
        ]:
            send(cmd, 2.5)

        if args.apn is not None:
            print(f"Setting APN={args.apn} ...", flush=True)
            send(f"AT+APN={args.apn}", 2.0)
            send("AT+APN=?", 2.0)
        if args.tdc is not None:
            print(f"Setting TDC={args.tdc} ...", flush=True)
            send(f"AT+TDC={args.tdc}", 2.0)
            send("AT+TDC=?", 2.0)
        if args.debug:
            print("Enabling AT+DEBUG=1 ...", flush=True)
            send("AT+DEBUG=1", 2.0)
        if args.dnscfg is not None:
            print(f"Setting DNSCFG={args.dnscfg} ...", flush=True)
            send(f"AT+DNSCFG={args.dnscfg}", 2.0)
            send("AT+DNSCFG=?", 2.0)

        if args.no_reboot:
            print("--no-reboot: skipping ATZ.", flush=True)
            return 0

        print("Rebooting with ATZ and capturing next cycle...", flush=True)
        send("ATZ", 1.0)
        end = time.monotonic() + args.capture
        while time.monotonic() < end:
            drain(5.0)
        return 0
    finally:
        try:
            ser.close()
        except Exception:
            pass
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
