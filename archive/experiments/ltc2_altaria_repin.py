#!/usr/bin/env python3
"""Force LTC2 SERVADDR/BKDNS to altaria 66.33.22.220:33239; TDC already 60.
Listen-only during uplink; re-pin after each End of upload until connected.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
IP, PORT = "66.33.22.220", "33239"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
PASS = "DrgN0-MqTt-7kR9wX2pL"
# load pass from file if present
envp = ROOT / "railway-mqtt.local.env"
if envp.is_file():
    for raw in envp.read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            PASS = raw.split("=", 1)[1].strip()


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_altaria_repin.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; force {IP},{PORT}; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(PASS, "***").replace(PIN, "***PIN***")
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

    def send(cmd: str, wait: float = 1.0) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def q(cmd: str) -> str:
        send(PIN, 0.2)
        for L in send(cmd, 1.0):
            t = L.strip()
            if (
                t
                and t != "OK"
                and not t.startswith("[")
                and not t.startswith("AT+")
                and "Password" not in t
                and "Failed" not in t
                and "MQTT" not in t
                and "Upload" not in t
                and "Attention" not in t
            ):
                return t
        return ""

    def pin_addr() -> dict[str, str]:
        send(PIN, 0.35)
        for _ in range(3):
            send(f"AT+SERVADDR={IP},{PORT}", 0.8)
            send(PIN, 0.2)
            send(f"AT+BKDNS=1,0,{IP},{PORT}", 0.8)
            send(PIN, 0.2)
        # keep auth/topics too
        for cmd in (
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={PASS}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            "AT+TDC=60",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
        ):
            send(PIN, 0.18)
            send(cmd, 0.75)
        cfg = {
            "SERVADDR": q("AT+SERVADDR=?"),
            "BKDNS": q("AT+BKDNS=?"),
            "TDC": q("AT+TDC=?"),
            "CLIENT": q("AT+CLIENT=?"),
            "PRO": q("AT+PRO=?"),
            "PUBTOPIC": q("AT+PUBTOPIC=?"),
            "UNAME": q("AT+UNAME=?"),
        }
        for k, v in cfg.items():
            log("CFG", f"{k}={v}")
        ok = IP in cfg["SERVADDR"] and PORT in cfg["SERVADDR"]
        log("TEST", f"PIN_ADDR ok={ok}")
        return cfg

    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "cycles": 0,
        "domain": "",
        "serv_ok": False,
    }
    cfg: dict[str, str] = {}

    # Try immediate pin in case UART still warm
    send(PIN, 0.5)
    if any("Password Correct" in L or "LTC2-CB" in L for L in send("AT+MODEL=?", 0.8)):
        cfg = pin_addr()
        flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")

    log("TEST", "LISTEN 600s — re-pin after each End of upload until CONNECTED")
    end = time.time() + 600
    in_upload = False
    while time.time() < end and flags["upload"] == 0:
        for L in read_lines(1.0):
            if "Upload start" in L:
                flags["cycles"] += 1
                in_upload = True
                log("MARK", f"CYCLE n={flags['cycles']}")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L[:140]}")
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected to the server" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", f"FAILED_SEND n={flags['failed']}")
            if "End of upload" in L or "NB module power-off" in L:
                in_upload = False
                read_lines(2.0)
                send(PIN, 0.4)
                cfg = pin_addr()
                flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get(
                    "SERVADDR", ""
                )
            if "Password timeout" in L or "Password Correct" in L:
                if in_upload:
                    continue
                # Already unlocked or just woke — pin immediately (don't wait for second PIN echo)
                send(PIN, 0.35)
                cfg = pin_addr()
                flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get(
                    "SERVADDR", ""
                )

    print("=== SUMMARY ===", flush=True)
    print(f"target={IP},{PORT}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["upload"] or flags["connected"] else 1)


if __name__ == "__main__":
    main()
