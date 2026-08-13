#!/usr/bin/env python3
"""Listen-only LTC2 serial for 2 uplink cycles; optional quiet SERVADDR peek on RDY."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
COM, BAUD = "COM8", 9600
PIN = "358613"


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_listen_only.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    print(f"Opened {COM}; listen-only; log={logpath}", flush=True)
    buf = b""
    peeked = False

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {s.replace(PIN, '***PIN***')}"
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

    def send(cmd: str, wait: float = 0.8) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    flags = {"cycles": 0, "opened": 0, "connected": 0, "upload": 0, "failed": 0, "domain": "", "serv": ""}
    end = time.time() + 200
    while time.time() < end and flags["cycles"] < 2:
        for L in read_lines(1.0):
            if (not peeked) and (L.strip() == "RDY" or "Signal Strength" in L):
                peeked = True
                send(PIN, 0.35)
                for cmd in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+TDC=?", "AT+CGPADDR=?", "AT+CSQ=?"):
                    for x in send(cmd, 0.9):
                        if x.strip() and x.strip() != "OK" and not x.startswith("["):
                            if "SERVADDR" in cmd:
                                flags["serv"] = x.strip()
                            log("PEEK", f"{cmd} -> {x.strip()}")
            if "Upload start" in L:
                flags["cycles"] += 1
                log("MARK", f"CYCLE {flags['cycles']}")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L}")
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", "FAILED_SEND")

    print("=== SUMMARY ===", flush=True)
    print(flags, flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()


if __name__ == "__main__":
    main()
