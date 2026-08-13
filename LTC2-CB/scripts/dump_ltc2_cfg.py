#!/usr/bin/env python3
"""Wake LTC2 on COM8, dump CFG with correct PIN. No AT writes except queries."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import serial

PIN = "358613"
COM = "COM8"
BAUD = 9600
ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_cfg_dump.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; log={logpath}", flush=True)
    print("\n>>> HOLD ACT 1-3s on LTC2 NOW <<<\n", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(PIN, "***PIN***")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(seconds: float) -> list[str]:
        nonlocal buf
        end = time.time() + seconds
        out: list[str] = []
        while time.time() < end:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    if text:
                        log("RX", text)
                        out.append(text)
            else:
                time.sleep(0.02)
        return out

    def send(cmd: str, wait: float = 1.4) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    unlocked = False
    deadline = time.time() + 120
    while time.time() < deadline:
        lines = send(PIN, 0.9)
        if any("Password Correct" in L for L in lines):
            unlocked = True
            break
        m = send("AT+MODEL=?", 1.0)
        if any("LTC2-CB" in L for L in m):
            unlocked = True
            break
    if not unlocked:
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)
    log("TEST", "UNLOCK_OK")
    send("AT+MODEL=?", 1.2)
    for q in [
        "AT+SERVADDR=?",
        "AT+BKDNS=?",
        "AT+PRO=?",
        "AT+CLIENT=?",
        "AT+UNAME=?",
        "AT+PUBTOPIC=?",
        "AT+SUBTOPIC=?",
        "AT+TLSMOD=?",
        "AT+MQOS=?",
        "AT+TDC=?",
    ]:
        send(q, 1.5)
    send("AT+CFG", 6.0)
    log("TEST", "CFG_DUMP_DONE")
    print(f"LOG={logpath}", flush=True)
    ser.close()


if __name__ == "__main__":
    main()
