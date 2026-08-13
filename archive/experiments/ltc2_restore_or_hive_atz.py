#!/usr/bin/env python3
"""Restore LTC2 Railway SERVADDR/auth after HiveMQ probe; optional ATZ boot uplink listen."""
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
RAIL_IP = "66.33.22.220"
RAIL_PORT = 33239
RAIL_USER = "dragino"
HIVE_IP = "52.59.36.109"
HIVE_PORT = 1883


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
    mode = os.environ.get("PROBE_MODE", "restore")  # restore | hive_atz
    logpath = ROOT / "logs" / f"{stamp}_ltc2_{mode}.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; mode={mode}; log={logpath}", flush=True)
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

    def listen_upload(seconds: float) -> dict[str, bool]:
        log("TEST", f"LISTEN {seconds}s")
        end = time.time() + seconds
        flags = {
            "opened": False,
            "connected": False,
            "upload": False,
            "failed": False,
            "serv_seen": False,
        }
        while time.time() < end:
            for L in read_lines(1.0):
                if "Opened the MQTT" in L:
                    flags["opened"] = True
                if "Successfully connected to the server" in L:
                    flags["connected"] = True
                    log("MARK", "CONNECTED")
                if "Upload data successfully" in L:
                    flags["upload"] = True
                    log("MARK", "UPLOAD_OK")
                if "Failed to send" in L:
                    flags["failed"] = True
                    log("MARK", "FAILED_SEND")
                if HIVE_IP in L or str(HIVE_PORT) in L or RAIL_IP in L:
                    flags["serv_seen"] = True
            if flags["connected"] or flags["upload"]:
                read_lines(10.0)
                break
        log("TEST", f"LISTEN_RESULT {flags}")
        return flags

    if not wake_unlock():
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    send("AT+SERVADDR=?", 1.3)
    send("AT+BKDNS=?", 1.3)
    send("AT+UNAME=?", 1.3)

    if mode == "hive_atz":
        log("TEST", "SET_HIVE_THEN_ATZ")
        for cmd, w in [
            (f"AT+SERVADDR={HIVE_IP},{HIVE_PORT}", 1.8),
            (f"AT+BKDNS=1,0,{HIVE_IP},{HIVE_PORT}", 1.8),
            ("AT+UNAME=NULL", 1.3),
            ("AT+PWD=NULL", 1.3),
            (f"AT+SERVADDR={HIVE_IP},{HIVE_PORT}", 1.5),
        ]:
            send(cmd, w)
        send("AT+SERVADDR=?", 1.3)
        log("TEST", "ATZ for boot uplink")
        send("ATZ", 1.5)
        print("\n>>> REBOOT — HOLD ACT ~12s later to unlock <<<\n", flush=True)
        time.sleep(12.0)
        if not wake_unlock(180.0):
            log("TEST", "post-ATZ unlock fail")
            ser.close()
            raise SystemExit(3)
        # Re-pin Hive immediately (PRO/BKDNS quirks)
        for cmd, w in [
            (f"AT+SERVADDR={HIVE_IP},{HIVE_PORT}", 1.6),
            (f"AT+BKDNS=1,0,{HIVE_IP},{HIVE_PORT}", 1.6),
            ("AT+UNAME=NULL", 1.2),
            ("AT+PWD=NULL", 1.2),
        ]:
            send(cmd, w)
        send("AT+SERVADDR=?", 1.3)
        send("AT+BKDNS=?", 1.3)
        flags = listen_upload(120.0)
        # Restore Railway after probe
        log("TEST", "RESTORE_AFTER_PROBE")
        for cmd, w in [
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 1.8),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 1.8),
            (f"AT+UNAME={RAIL_USER}", 1.3),
            (f"AT+PWD={mqtt_pass}", 1.3),
        ]:
            send(cmd, w)
        for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?", "AT+PUBTOPIC=?"):
            send(q, 1.3)
        print("=== SUMMARY ===", flush=True)
        print(flags, flush=True)
        print(f"LOG={logpath}", flush=True)
        ser.close()
        raise SystemExit(0 if flags.get("connected") or flags.get("upload") else 1)

    # restore only
    log("TEST", "RESTORE_RAILWAY")
    for cmd, w in [
        (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 1.8),
        (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 1.8),
        (f"AT+UNAME={RAIL_USER}", 1.3),
        (f"AT+PWD={mqtt_pass}", 1.3),
        (f"AT+CLIENT=ltc2", 1.2),
        (f"AT+PUBTOPIC=dragino/ltc2/up", 1.2),
        (f"AT+SUBTOPIC=dragino/ltc2/down", 1.2),
    ]:
        send(cmd, w)
    for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?", "AT+PUBTOPIC=?", "AT+PRO=?"):
        send(q, 1.3)
    log("TEST", "RESTORE_DONE")
    print(f"LOG={logpath}", flush=True)
    ser.close()


if __name__ == "__main__":
    main()
