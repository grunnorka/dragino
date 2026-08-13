#!/usr/bin/env python3
"""One-shot: unlock, dump CFG, set TDC=180, monitor for fail-of-data."""
from __future__ import annotations
import sys, time, re
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
from monitor import load_dotenv, resolve_pin

PORT = "COM8"
BAUD = 9600
TDC_SEC = 180
MONITOR_S = 180  # watch ~3 min after config
LOG_DIR = ROOT / "logs"

def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    session = datetime.now().strftime("%Y%m%d_%H%M%S") + "_tbdiag"
    raw_path = LOG_DIR / f"{session}.raw.log"
    print(f"Log: {raw_path}", flush=True)

    try:
        ser = serial.Serial(PORT, BAUD, timeout=0.25, write_timeout=2)
    except serial.SerialException as e:
        print(f"ERROR opening {PORT}: {e}", file=sys.stderr)
        print("Free COM8 (close other terminals/monitor.py) and retry.", file=sys.stderr)
        return 3

    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    buf = bytearray()
    all_lines: list[str] = []

    def log(direction: str, text: str) -> None:
        line = f"{utc()} {direction} {text}"
        with raw_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)
        if direction == "RX":
            all_lines.append(text)

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
                got.append(text)
        return got

    def send(cmd: str) -> None:
        payload = (cmd.rstrip("\r\n") + "\r\n").encode("utf-8")
        ser.write(payload)
        ser.flush()
        # never log raw PIN value
        shown = "******" if cmd.strip() == pin or cmd.strip() == f"AT+PIN={pin}" else cmd
        log("TX", shown)

    log("SYS", f"Opened {PORT} @ {BAUD}; waiting for wake / sending PIN")

    # Wake / unlock: send PIN a few times (device may sleep)
    unlocked = False
    for attempt in range(1, 16):
        send(pin)
        got = drain(2.0)
        joined = "\n".join(got).lower()
        if any("ok" in g.lower() or "at+" in g.lower() or "password" in g.lower() for g in got):
            # try AT after pin
            send("AT")
            got2 = drain(1.5)
            if any(g.strip().upper() == "OK" for g in got2) or any("OK" in g for g in got2):
                unlocked = True
                log("SYS", f"Unlocked on attempt {attempt}")
                break
        if any("ok" == g.strip().lower() for g in got):
            unlocked = True
            log("SYS", f"PIN OK on attempt {attempt}")
            break
        log("SYS", f"Unlock attempt {attempt}/15 — wake device if quiet")

    if not unlocked:
        # still proceed — maybe already in AT mode after wake button
        log("SYS", "No clear unlock OK yet; trying AT commands anyway (press wake button)")
        send("AT")
        drain(2.0)

    send("AT+DEBUG=1")
    drain(2.0)

    # Read TDC before
    log("SYS", "=== TDC BEFORE ===")
    send("AT+TDC=?")
    drain(3.0)

    # Full config dump (ThingsBoard etc.)
    log("SYS", "=== AT+CFG ===")
    send("AT+CFG")
    drain(8.0)

    # Also try related queries if present on this firmware
    for cmd in ("AT+SERVADDR=?", "AT+APN=?", "AT+CLIENT=?", "AT+PRO=?", "AT+PDPORT=?", "AT+TLSPUBKEY=?"):
        send(cmd)
        drain(2.0)

    # Set cycle to 180s
    log("SYS", "=== SET TDC=180 ===")
    send(f"AT+TDC={TDC_SEC}")
    drain(2.0)
    send("AT+TDC=?")
    drain(3.0)

    # Trigger / observe uplink path if possible
    for cmd in ("AT+LDATA", "AT+CSQ", "AT+CEREG?"):
        send(cmd)
        drain(2.5)

    log("SYS", f"=== MONITOR {MONITOR_S}s for fail-of-data / uplink ===")
    log("SYS", "Wake/press button if device sleeps; watching serial...")
    end = time.monotonic() + MONITOR_S
    last_nudge = 0.0
    while time.monotonic() < end:
        drain(1.0)
        now = time.monotonic()
        # light keepalive every 30s without changing config
        if now - last_nudge >= 30:
            last_nudge = now
            send("AT")
            drain(1.0)

    # Final TDC confirm
    log("SYS", "=== TDC FINAL READBACK ===")
    send("AT+TDC=?")
    drain(3.0)

    # Summarize interesting lines
    log("SYS", "=== SUMMARY FILTER ===")
    keys = re.compile(
        r"fail|error|things|mqtt|http|tdc|serv|token|url|host|port|tls|ssl|upload|ldata|cfg|ok|reject|401|403|404|timeout",
        re.I,
    )
    hits = [l for l in all_lines if keys.search(l)]
    for h in hits[-80:]:
        log("HIT", h)

    ser.close()
    log("SYS", f"Done. Full log: {raw_path}")
    print(f"\nFULL_LOG={raw_path}", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
