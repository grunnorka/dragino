#!/usr/bin/env python3
"""LTC2 port proof: set HiveMQ :1883, ATZ, listen boot uplink on COM8 (no 2nd unlock needed), restore Railway."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM = "COM8"
BAUD = 9600
HIVE_IP, HIVE_PORT = "52.59.36.109", 1883
RAIL_IP, RAIL_PORT = "66.33.22.220", 33239
RAIL_USER = "dragino"


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_hive1883_atz_boot.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(mqtt_pass, "***").replace(PIN, "***PIN***")
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

    def wake_unlock(timeout: float = 180.0) -> bool:
        print("\n>>> HOLD ACT 1-3s on LTC2 NOW <<<\n", flush=True)
        log("TEST", f"WAKE_UNLOCK {timeout}s")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any("Password Correct" in L for L in send(PIN, 0.9)):
                log("TEST", "UNLOCK_OK")
                return True
            if any("LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
                log("TEST", "UNLOCK_OK already")
                return True
        return False

    if not wake_unlock():
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+PRO=?"):
        send(q, 1.3)

    log("TEST", "SET_HIVE_1883")
    for cmd, w in [
        (f"AT+SERVADDR={HIVE_IP},{HIVE_PORT}", 1.8),
        (f"AT+BKDNS=1,0,{HIVE_IP},{HIVE_PORT}", 1.8),
        ("AT+UNAME=NULL", 1.3),
        ("AT+PWD=NULL", 1.3),
        (f"AT+SERVADDR={HIVE_IP},{HIVE_PORT}", 1.5),
        (f"AT+BKDNS=1,0,{HIVE_IP},{HIVE_PORT}", 1.5),
    ]:
        send(cmd, w)
    send("AT+SERVADDR=?", 1.3)
    send("AT+BKDNS=?", 1.3)

    log("TEST", "ATZ — listen boot uplink (no unlock needed for modem logs)")
    send("ATZ", 1.5)

    flags = {
        "boot": False,
        "opened": False,
        "connected": False,
        "upload": False,
        "failed": False,
        "hive_ip_seen": False,
        "rail_ip_seen": False,
    }
    deadline = time.time() + 130
    while time.time() < deadline:
        for L in read_lines(1.0):
            if "bootloader" in L.lower() or "Image Version" in L or "NB-IoT Stack" in L:
                flags["boot"] = True
            if HIVE_IP in L:
                flags["hive_ip_seen"] = True
            if RAIL_IP in L:
                flags["rail_ip_seen"] = True
            if "Opened the MQTT" in L:
                flags["opened"] = True
                log("MARK", "OPENED")
            if "Successfully connected to the server" in L:
                flags["connected"] = True
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] = True
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                flags["failed"] = True
                log("MARK", "FAILED_SEND")
        if flags["connected"] or flags["upload"] or (
            flags["opened"] and flags["failed"] and flags["boot"]
        ):
            # allow a little more if we only saw open+fail
            if flags["connected"] or flags["upload"]:
                read_lines(8.0)
                break
            if flags["failed"] and time.time() > deadline - 40:
                break

    log("TEST", f"BOOT_PROBE {flags}")

    # Restore Railway — may need ACT again
    print("\n>>> HOLD ACT to restore Railway settings <<<\n", flush=True)
    restored = False
    if wake_unlock(120.0):
        log("TEST", "RESTORE_RAILWAY")
        for cmd, w in [
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 1.8),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 1.8),
            (f"AT+UNAME={RAIL_USER}", 1.3),
            (f"AT+PWD={mqtt_pass}", 1.3),
            ("AT+CLIENT=ltc2", 1.2),
            ("AT+PUBTOPIC=dragino/ltc2/up", 1.2),
            ("AT+SUBTOPIC=dragino/ltc2/down", 1.2),
            ("AT+PRO=3,5", 1.5),
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 1.5),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 1.5),
        ]:
            send(cmd, w)
        for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?", "AT+PUBTOPIC=?"):
            send(q, 1.3)
        restored = True
    else:
        log("TEST", "RESTORE_UNLOCK_FAIL — device may remain on HiveMQ :1883")

    print("=== SUMMARY ===", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"restored={restored}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)
    ser.close()
    ok = flags["connected"] or flags["upload"]
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
