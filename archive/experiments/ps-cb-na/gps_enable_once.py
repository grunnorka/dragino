#!/usr/bin/env python3
"""One-shot sequential GPS enable + verify on PS-CB-NA (waits for OK between cmds)."""

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
LOG_DIR = ROOT / "logs"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    session = datetime.now().strftime("%Y%m%d_%H%M%S") + "_gps"
    raw_path = LOG_DIR / f"{session}.raw.log"
    print(f"Log: {raw_path}", flush=True)

    ser = serial.Serial(PORT, BAUD, timeout=0.2, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    buf = bytearray()
    lines: list[str] = []

    def log(direction: str, text: str) -> None:
        line = f"{utc()} {direction} {text}"
        with raw_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def drain(seconds: float) -> list[str]:
        end = time.monotonic() + seconds
        got: list[str] = []
        while time.monotonic() < end:
            chunk = ser.read(4096)
            if not chunk:
                continue
            buf.extend(chunk)
            while True:
                i = buf.find(b"\n")
                if i < 0:
                    break
                raw = buf[: i + 1]
                del buf[: i + 1]
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                log("RX", text)
                lines.append(text)
                got.append(text)
        return got

    def send(cmd: str) -> None:
        payload = (cmd.rstrip("\r\n") + "\r\n").encode("utf-8")
        ser.write(payload)
        ser.flush()
        log("TX", cmd)

    def send_wait(cmd: str, wait: float = 2.0, expect_ok: bool = True) -> list[str]:
        before = len(lines)
        send(cmd)
        got = drain(wait)
        # also include any lines appended
        got = lines[before:]
        if expect_ok and not any(re.fullmatch(r"OK", x.strip(), re.I) for x in got):
            print(f"  !! no OK yet for {cmd!r} (got {got!r})", flush=True)
        return got

    log("SYS", f"PORT OPEN {PORT} @ {BAUD}")
    drain(1.0)

    # Wake / unlock retries
    unlocked = False
    for attempt in range(1, 13):
        send(pin)
        got = drain(3.0)
        if any("password correct" in x.lower() or "correct" in x.lower() for x in got):
            unlocked = True
            print(f"Unlocked on attempt {attempt}", flush=True)
            break
        if got:
            # some traffic — try AT ping
            send_wait("AT", wait=1.5)
            unlocked = True
            break
        print(f"No RX — unlock retry {attempt}/12 (press device button to wake)...", flush=True)

    if not unlocked and not lines:
        log("SYS", "SILENT: device did not respond. Wake with button and re-run.")
        ser.close()
        print(f"Saved: {raw_path}")
        return 1

    send_wait("AT+DEBUG=1", wait=1.5)
    send_wait("AT+GPS=?", wait=2.0)
    send_wait("AT+GPS=1", wait=2.5)
    gps_q = send_wait("AT+GPS=?", wait=2.5)
    send_wait("AT+GNSST=?", wait=2.0)
    send_wait("AT+GNSST=120", wait=2.5)
    send_wait("AT+GNSST=?", wait=2.0)
    send_wait("AT+GTDC=?", wait=2.0)
    send_wait("AT+GTDC=1", wait=2.5)
    send_wait("AT+GTDC=?", wait=2.0)
    send_wait("AT+CFG", wait=8.0, expect_ok=False)

    # Listen for GNSS/search/uplink payloads (cold start can take full GNSST)
    print("Listening 150s for latitude/longitude / GNSS activity...", flush=True)
    print("If indoors, move outdoors with clear sky view and press the wake button.", flush=True)
    drain(150.0)

    lat_hits = [x for x in lines if "latitude" in x.lower() or "longitude" in x.lower()]
    gps_vals = []
    for i, x in enumerate(lines):
        if re.fullmatch(r"[01]", x.strip()) and i + 1 < len(lines) and lines[i + 1].strip().upper() == "OK":
            # likely AT+GPS=? reply — ambiguous; track all
            gps_vals.append(x.strip())

    print("--- summary ---", flush=True)
    print(f"unlocked={unlocked} lines={len(lines)}", flush=True)
    print(f"AT+GPS=? replies near OK: {gps_vals}", flush=True)
    print(f"lat/lon lines: {len(lat_hits)}", flush=True)
    for x in lat_hits[-5:]:
        print(f"  {x[:300]}", flush=True)
    # Heuristic: last AT+GPS=? after set should return 1
    gps_enabled = any(re.search(r"\bGPS[=:\s]*1\b", x, re.I) for x in lines)
    if "1" in gps_vals:
        gps_enabled = True
    print(f"gps_enabled_heuristic={gps_enabled}", flush=True)
    print(f"Saved: {raw_path}", flush=True)
    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
