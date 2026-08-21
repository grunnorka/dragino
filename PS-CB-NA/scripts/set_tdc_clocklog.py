#!/usr/bin/env python3
"""Set TDC (upload interval) and CLOCKLOG (sampling) on the attached device.

Simplified: no idle-wait — unlock directly (like quick_status.py).
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
TDC = "43200"                       # 12 hours (twice daily)
CLOCKLOG = "1,65535,240,6"          # sample every 4 h, 6 log slots (6/day)


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

    print(f"Port={args.port}  TDC={TDC}  CLOCKLOG={CLOCKLOG}", flush=True)
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

    # ── Unlock ───────────────────────────────────────────────────────
    unlocked = False
    for i in range(1, 30):
        got = send(pin, 2.0)
        if any("Password Correct" in g for g in got):
            unlocked = True
            log("SYS", f"unlock_ok attempt {i}")
            break
        send("AT", 1.0)

    if not unlocked:
        print("Could not unlock (device may be sleeping).", flush=True)
        ser.close()
        return 1

    # ── Set TDC ──────────────────────────────────────────────────────
    print(f"\nSetting TDC={TDC} ...", flush=True)
    send(f"AT+TDC={TDC}", 2.0)
    time.sleep(1.0)
    verify_tdc = send("AT+TDC=?", 2.5)

    # ── Set CLOCKLOG ─────────────────────────────────────────────────
    print(f"\nSetting CLOCKLOG={CLOCKLOG} ...", flush=True)
    send(f"AT+CLOCKLOG={CLOCKLOG}", 2.0)
    time.sleep(1.0)
    verify_clk = send("AT+CLOCKLOG=?", 2.5)

    # ── Summary ──────────────────────────────────────────────────────
    tdc_ok = verify_tdc and any(TDC in line for line in verify_tdc)
    clk_ok = verify_clk and any("240" in line and "6" in line for line in verify_clk)
    print(f"\n=== Verification ===", flush=True)
    print(f"TDC      : {'OK ✓' if tdc_ok else 'FAILED ✗'}", flush=True)
    print(f"CLOCKLOG : {'OK ✓' if clk_ok else 'FAILED ✗'}", flush=True)

    # ── Reboot ───────────────────────────────────────────────────────
    print("\nRebooting (ATZ) ...", flush=True)
    send("ATZ", 3.0)

    ser.close()
    if tdc_ok and clk_ok:
        print("Done — settings applied and verified.", flush=True)
        return 0
    else:
        print("Done — but verification FAILED; check output above.", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
