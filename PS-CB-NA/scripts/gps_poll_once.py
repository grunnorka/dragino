#!/usr/bin/env python3
"""One-shot GPS poll: unlock, query GPS AT settings, LDATA, try QGPSLOC, listen for lat/lon."""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
from monitor import load_dotenv, resolve_pin  # noqa: E402

PORT = "COM8"
BAUD = 9600
LISTEN_S = 180  # ~3 min for an uplink / button activate


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    session = datetime.now().strftime("%Y%m%d_%H%M%S") + "_gps_poll"
    raw_path = log_dir / f"{session}.raw.log"
    print(f"Log: {raw_path}", flush=True)
    print(f"Listen window: {LISTEN_S}s (outdoor sky / button activate helps)", flush=True)

    ser = serial.Serial(PORT, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    buf = bytearray()
    lines: list[str] = []
    latlon: list[str] = []
    gps_vals: list[str] = []

    def log(direction: str, text: str) -> None:
        line = f"{utc()} {direction} {text}"
        print(line, flush=True)
        with raw_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def drain(wait: float = 0.0) -> list[str]:
        if wait > 0:
            time.sleep(wait)
        got: list[str] = []
        deadline = time.monotonic() + max(wait, 0.05)
        while time.monotonic() < deadline or ser.in_waiting:
            chunk = ser.read(max(ser.in_waiting, 1))
            if not chunk:
                if time.monotonic() >= deadline and not ser.in_waiting:
                    break
                continue
            buf.extend(chunk)
            while True:
                i = buf.find(b"\n")
                if i < 0:
                    break
                raw = bytes(buf[: i + 1])
                del buf[: i + 1]
                text = raw.decode("utf-8", errors="replace").strip("\r\n")
                if not text:
                    continue
                log("RX", text)
                lines.append(text)
                got.append(text)
                if re.search(r"latitude\s*[:=]", text, re.I):
                    latlon.append(text)
                m = re.search(r"(?:AT\+GPS=|\+GPS:|GPS[=:\s]+)([01])\b", text, re.I)
                if m:
                    gps_vals.append(m.group(1))
            deadline = time.monotonic() + 0.15
        return got

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        payload = (cmd + "\r\n").encode("ascii")
        log("TX", cmd)
        ser.write(payload)
        ser.flush()
        return drain(wait)

    # Unlock
    send(pin, wait=2.5)
    send("AT", wait=1.5)

    # GPS config queries (enable state / search window / interval — NOT lat/lon)
    send("AT+GPS=?", wait=2.0)
    send("AT+GNSST=?", wait=2.0)
    send("AT+GTDC=?", wait=2.0)

    # Last upload may include cached latitude/longitude
    send("AT+LDATA", wait=3.0)

    # Quectel GNSS location (likely unsupported on Dragino AT console — probe)
    send("AT+QGPSLOC=2", wait=2.5)
    send("AT+QGPS?", wait=2.0)

    # Listen for uplink console lines with lat/lon
    t0 = time.monotonic()
    while time.monotonic() - t0 < LISTEN_S:
        drain(0.4)

    print("--- SUMMARY ---", flush=True)
    print(f"AT+GPS enable replies: {gps_vals}", flush=True)
    print(f"latitude/longitude lines: {len(latlon)}", flush=True)
    for x in latlon:
        print(f"  FIX: {x}", flush=True)
    ldata = [x for x in lines if "LDATA" in x.upper() or re.search(r"latitude", x, re.I)]
    if not latlon:
        print("No live lat/lon this session.", flush=True)
        print("Wiki: position is in uplink JSON (latitude/longitude/gps_time), not an AT poll.", flush=True)
    ser.close()
    return 0 if latlon else 1


if __name__ == "__main__":
    raise SystemExit(main())
