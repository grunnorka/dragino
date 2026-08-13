#!/usr/bin/env python3
"""Idle → unlock → set TDC=120 (Railway intact) → verify → monitor 3 cycles."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
from monitor import load_dotenv, resolve_pin  # noqa: E402
from railway_mqtt import load_config, servaddr  # noqa: E402

PORT, BAUD = "COM8", 9600
TDC = 120  # previously used on this unit; 60 OK may not stick
DEVICE_ID = "ps-cb"
CYCLES, MAX_S = 3, 10 * 60


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    cfg = load_config()
    pwd = cfg["MQTT_PASS"]
    addr = servaddr(cfg, use_ip=True)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logpath = logs / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pscb_tdc120.raw.log"
    print(f"Log={logpath} Railway={addr} TDC={TDC}", flush=True)

    ser = serial.Serial(PORT, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()
    summary: dict = {"cycles": []}

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***").replace(pwd, "***PASS***")
        row = f"{utc()} {tag} {safe}"
        print(row, flush=True)
        logpath.open("a", encoding="utf-8").write(row + "\n")

    def drain(s: float) -> list[str]:
        end = time.monotonic() + s
        out: list[str] = []
        while time.monotonic() < end:
            chunk = ser.read(4096)
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
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd.replace(pwd, "***PASS***")
        log("TX", shown)
        ser.write((cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
        ser.flush()
        return drain(wait)

    def unlock() -> bool:
        for i in range(1, 20):
            got = send(pin, 2.2)
            if any("Password Correct" in g for g in got):
                log("SYS", f"unlock_ok {i}")
                return True
            send(f"AT+PIN={pin}", 1.0)
            if any(g.strip().upper() == "OK" for g in send("AT", 1.2)):
                # confirm with a harmless query
                if any("OK" in g for g in send("AT", 1.0)):
                    log("SYS", f"AT_OK {i}")
                    return True
        return False

    def q(cmd: str) -> str:
        vals = []
        for L in send(cmd, 2.0):
            t = L.strip()
            if not t or t == "OK" or t.startswith(("AT+", "[", "Attention")):
                continue
            if "assword" in t or "Searching" in t or "NB " in t:
                continue
            vals.append(t)
        return vals[0] if vals else ""

    # wait idle
    log("SYS", "WAIT_IDLE")
    quiet = None
    end = time.monotonic() + 90
    while time.monotonic() < end:
        lines = drain(1.0)
        for L in lines:
            if "Upload start" in L or "Searching for location" in L:
                quiet = None
            if "End of upload" in L or "power-off successful" in L:
                quiet = time.monotonic()
        if quiet and time.monotonic() - quiet > 5:
            break
        if not lines and quiet is None:
            if not drain(3):
                break

    if not unlock():
        log("SYS", "UNLOCK_FAILED")
        ser.close()
        return 4

    pre = {
        "servaddr": q("AT+SERVADDR=?"),
        "pro": q("AT+PRO=?"),
        "tdc": q("AT+TDC=?"),
        "client": q("AT+CLIENT=?"),
        "uname": q("AT+UNAME=?"),
        "pub": q("AT+PUBTOPIC=?"),
        "gps": q("AT+GPS=?"),
    }
    log("SYS", f"PRE {pre}")
    summary["pre"] = pre

    # Re-unlock if needed then apply Railway + TDC quickly
    unlock()
    cmds = [
        ("AT+PRO=3,5", 2.0),
        (f"AT+SERVADDR={addr}", 2.0),
        (f"AT+BKDNS=1,0,{cfg['MQTT_FALLBACK_IP']},{cfg['MQTT_PORT']}", 2.0),
        (f"AT+CLIENT={DEVICE_ID}", 1.5),
        (f"AT+UNAME={cfg['MQTT_USER']}", 1.5),
        (f"AT+PWD={pwd}", 1.5),
        (f"AT+PUBTOPIC=dragino/{DEVICE_ID}/up", 1.5),
        (f"AT+SUBTOPIC=dragino/{DEVICE_ID}/down", 1.5),
        ("AT+GPS=0", 1.5),
        (f"AT+TDC={TDC}", 2.5),
    ]
    for c, w in cmds:
        send(c, w)

    # verify TDC with retries
    tdc_ok = False
    for attempt in range(1, 5):
        unlock()
        send(f"AT+TDC={TDC}", 2.5)
        val = q("AT+TDC=?")
        log("SYS", f"TDC_TRY {attempt} readback={val}")
        if val == str(TDC):
            tdc_ok = True
            break
        time.sleep(0.5)

    if not tdc_ok:
        # try 60 as alternate short
        unlock()
        send("AT+TDC=60", 2.5)
        val60 = q("AT+TDC=?")
        log("SYS", f"TDC_60_TRY readback={val60}")
        if val60 == "60":
            TDC_FINAL = 60
            tdc_ok = True
        else:
            TDC_FINAL = val60 or "unknown"
    else:
        TDC_FINAL = TDC

    post = {
        "servaddr": q("AT+SERVADDR=?"),
        "pro": q("AT+PRO=?"),
        "tdc": q("AT+TDC=?"),
        "client": q("AT+CLIENT=?"),
        "uname": q("AT+UNAME=?"),
        "pub": q("AT+PUBTOPIC=?"),
        "gps": q("AT+GPS=?"),
        "bkdns": q("AT+BKDNS=?"),
    }
    log("SYS", f"POST {post} tdc_ok={tdc_ok} TDC_FINAL={TDC_FINAL}")
    summary["post"] = post
    summary["tdc_final"] = TDC_FINAL
    send("AT+DEBUG=1", 1.2)

    deadline = time.monotonic() + MAX_S
    cycle = None
    log("SYS", f"MONITOR {CYCLES} cycles")
    while time.monotonic() < deadline and len(summary["cycles"]) < CYCLES:
        for L in drain(1.0):
            low = L.lower()
            if "start of upload" in low:
                if cycle and "end" not in cycle:
                    cycle["end"] = "interrupted"
                    summary["cycles"].append(cycle)
                cycle = {
                    "n": len(summary["cycles"]) + 1,
                    "start": utc(),
                    "ok": False,
                    "failed": False,
                    "conn": False,
                    "sub": False,
                }
                log("MARK", f"CYCLE_START {cycle['n']}")
            if not cycle:
                continue
            if "successfully connected" in low:
                cycle["conn"] = True
            if "upload data successfully" in low:
                cycle["ok"] = True
            if "failed to send" in low:
                cycle["failed"] = True
            if "subscribe" in low and "success" in low:
                cycle["sub"] = True
            if "end of upload" in low or "power-off successful" in low:
                cycle["end"] = utc()
                summary["cycles"].append(cycle)
                log("MARK", f"CYCLE_END {cycle}")
                cycle = None

    if cycle:
        cycle["end"] = "timeout"
        summary["cycles"].append(cycle)

    unlock()
    summary["final"] = {
        "servaddr": q("AT+SERVADDR=?"),
        "pro": q("AT+PRO=?"),
        "tdc": q("AT+TDC=?"),
    }
    log("SYS", f"FINAL {summary['final']}")
    ser.close()
    print("=== SUMMARY ===", flush=True)
    for k in ("pre", "post", "tdc_final", "final"):
        print(f"{k.upper()}={summary.get(k)}", flush=True)
    for c in summary["cycles"]:
        print(f"CYCLE={c}", flush=True)
    print(f"LOG={logpath}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
