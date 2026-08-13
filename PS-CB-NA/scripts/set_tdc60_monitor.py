#!/usr/bin/env python3
"""Set short TDC on PS-CB-NA (Railway MQTT), monitor a few uplink cycles."""

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

PORT = "COM8"
BAUD = 9600
DEVICE_ID = "ps-cb"
TDC = 60  # short test interval (seconds)
MONITOR_CYCLES = 3
MONITOR_MAX_S = 8 * 60
LOG_DIR = ROOT / "logs"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    cfg = load_config()
    mqtt_pass = cfg["MQTT_PASS"]
    if not pin or not mqtt_pass:
        print("ERROR: missing DRAGINO_PIN or MQTT_PASS", file=sys.stderr)
        return 2

    addr = servaddr(cfg, use_ip=True)
    user = cfg["MQTT_USER"]
    pub = f"dragino/{DEVICE_ID}/up"
    sub = f"dragino/{DEVICE_ID}/down"
    bkdns = f"1,0,{cfg['MQTT_FALLBACK_IP']},{cfg['MQTT_PORT']}"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpath = LOG_DIR / f"{stamp}_pscb_tdc60_railway.raw.log"
    print(f"Log: {logpath}", flush=True)
    print(f"Railway SERVADDR={addr} TDC={TDC}", flush=True)

    try:
        ser = serial.Serial(PORT, BAUD, timeout=0.25, write_timeout=2)
    except serial.SerialException as e:
        print(f"ERROR opening {PORT}: {e}", file=sys.stderr)
        return 3

    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    buf = bytearray()
    summary: dict = {"cycles": [], "pre": {}, "post": {}, "final": {}}

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***").replace(mqtt_pass, "***PASS***")
        row = f"{utc()} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def drain(seconds: float) -> list[str]:
        end = time.monotonic() + seconds
        got: list[str] = []
        while time.monotonic() < end:
            chunk = ser.read(4096)
            if chunk:
                buf.extend(chunk)
                while True:
                    i = buf.find(b"\n")
                    if i < 0:
                        break
                    raw = bytes(buf[: i + 1])
                    del buf[: i + 1]
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        log("RX", text)
                        got.append(text)
            else:
                time.sleep(0.02)
        return got

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        if mqtt_pass in shown:
            shown = shown.replace(mqtt_pass, "***PASS***")
        log("TX", shown)
        ser.write((cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
        ser.flush()
        return drain(wait)

    def unlock(n: int = 14) -> bool:
        for i in range(1, n + 1):
            if any("Password Correct" in g for g in send(pin, 2.0)):
                log("SYS", f"unlock_ok {i}")
                return True
            send(f"AT+PIN={pin}", 1.0)
            if any(g.strip().upper() == "OK" for g in send("AT", 1.0)):
                log("SYS", f"AT_OK {i}")
                return True
        return False

    def q(cmd: str) -> str:
        for L in send(cmd, 1.6):
            t = L.strip()
            if t and t != "OK" and not t.startswith("AT+") and not t.startswith("["):
                if "assword" not in t:
                    return t
        return ""

    drain(1.0)
    # wait briefly if mid-upload
    for _ in range(25):
        lines = drain(1.0)
        if any("End of upload" in L or "power-off successful" in L for L in lines):
            drain(4.0)
            break
        if not lines:
            break

    if not unlock():
        log("SYS", "UNLOCK_FAILED")
        ser.close()
        return 4

    summary["pre"] = {
        "servaddr": q("AT+SERVADDR=?"),
        "pro": q("AT+PRO=?"),
        "bkdns": q("AT+BKDNS=?"),
        "tdc": q("AT+TDC=?"),
        "client": q("AT+CLIENT=?"),
        "uname": q("AT+UNAME=?"),
        "pub": q("AT+PUBTOPIC=?"),
        "gps": q("AT+GPS=?"),
    }
    log("SYS", f"PRE {summary['pre']}")

    # Ensure Railway + short TDC; disable GPS to avoid long GNSS search before uplink
    for cmd, w in [
        ("AT+PRO=3,5", 2.0),
        (f"AT+SERVADDR={addr}", 2.0),
        (f"AT+BKDNS={bkdns}", 2.0),
        (f"AT+CLIENT={DEVICE_ID}", 1.5),
        (f"AT+UNAME={user}", 1.5),
        (f"AT+PWD={mqtt_pass}", 1.5),
        (f"AT+PUBTOPIC={pub}", 1.5),
        (f"AT+SUBTOPIC={sub}", 1.5),
        ("AT+MQOS=1", 1.2),
        ("AT+TLSMOD=0,0", 1.2),
        ("AT+GPS=0", 1.5),
        (f"AT+TDC={TDC}", 1.5),
        (f"AT+SERVADDR={addr}", 1.5),
    ]:
        send(cmd, w)

    unlock(6)
    summary["post"] = {
        "servaddr": q("AT+SERVADDR=?"),
        "pro": q("AT+PRO=?"),
        "bkdns": q("AT+BKDNS=?"),
        "tdc": q("AT+TDC=?"),
        "client": q("AT+CLIENT=?"),
        "uname": q("AT+UNAME=?"),
        "pub": q("AT+PUBTOPIC=?"),
        "sub": q("AT+SUBTOPIC=?"),
        "gps": q("AT+GPS=?"),
    }
    log("SYS", f"POST {summary['post']}")
    send("AT+DEBUG=1", 1.2)

    log("SYS", f"MONITOR {MONITOR_CYCLES} cycles max {MONITOR_MAX_S}s")
    deadline = time.monotonic() + MONITOR_MAX_S
    cycle = None
    while time.monotonic() < deadline and len(summary["cycles"]) < MONITOR_CYCLES:
        for L in drain(1.0):
            low = L.lower()
            if "start of upload" in low:
                if cycle and cycle.get("end") is None:
                    cycle["end"] = "interrupted"
                    summary["cycles"].append(cycle)
                cycle = {
                    "n": len(summary["cycles"]) + 1,
                    "start": utc(),
                    "upload_ok": False,
                    "failed": False,
                    "connected": False,
                    "sub": False,
                    "notes": [],
                }
                log("MARK", f"CYCLE_START n={cycle['n']}")
            if not cycle:
                continue
            if "successfully connected" in low:
                cycle["connected"] = True
            if "upload data successfully" in low:
                cycle["upload_ok"] = True
            if "failed to send" in low:
                cycle["failed"] = True
            if "subscribe" in low and "success" in low:
                cycle["sub"] = True
            if "167.235" in L or "hivemq" in low:
                cycle["notes"].append("wrong_broker")
            if "end of upload" in low or "power-off successful" in low:
                cycle["end"] = utc()
                summary["cycles"].append(cycle)
                log(
                    "MARK",
                    f"CYCLE_END n={cycle['n']} ok={cycle['upload_ok']} "
                    f"failed={cycle['failed']} conn={cycle['connected']} sub={cycle['sub']}",
                )
                cycle = None

    if cycle:
        cycle["end"] = "timeout"
        summary["cycles"].append(cycle)

    unlock(5)
    summary["final"] = {
        "servaddr": q("AT+SERVADDR=?"),
        "pro": q("AT+PRO=?"),
        "tdc": q("AT+TDC=?"),
        "bkdns": q("AT+BKDNS=?"),
    }
    log("SYS", f"FINAL {summary['final']}")
    ser.close()

    print("=== SUMMARY ===", flush=True)
    print(f"BROKER={addr} proxy={cfg['MQTT_HOST']}:{cfg['MQTT_PORT']}", flush=True)
    print(f"PRE={summary['pre']}", flush=True)
    print(f"POST={summary['post']}", flush=True)
    print(f"FINAL={summary['final']}", flush=True)
    for c in summary["cycles"]:
        print(f"CYCLE={c}", flush=True)
    print(f"LOG={logpath}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
