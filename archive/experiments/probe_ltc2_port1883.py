#!/usr/bin/env python3
"""Brief LTC2 path proof: swap SERVADDR/BKDNS to HiveMQ :1883, listen for connect, restore Railway.

Does NOT re-apply full Railway AT set; only port/host + auth for anonymous HiveMQ, then restore.
"""
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
# Public HiveMQ (anonymous :1883) — IP avoids DNS quirks on NB
HIVE_IP = "52.59.36.109"
HIVE_PORT = 1883
RAIL_IP = "66.33.22.220"
RAIL_PORT = 33239
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
    logpath = ROOT / "logs" / f"{stamp}_ltc2_port1883_probe.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; log={logpath}", flush=True)
    print("\n>>> HOLD ACT 1-3s on LTC2 when prompted <<<\n", flush=True)
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

    def wake_unlock(timeout: float = 120.0) -> bool:
        print("\n>>> HOLD ACT 1-3s NOW <<<\n", flush=True)
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

    # Baseline
    for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?"):
        send(q, 1.3)

    log("TEST", "POINT_HIVEMQ_1883")
    for cmd, w in [
        (f"AT+SERVADDR={HIVE_IP},{HIVE_PORT}", 1.8),
        (f"AT+BKDNS=1,0,{HIVE_IP},{HIVE_PORT}", 1.8),
        ("AT+UNAME=NULL", 1.3),
        ("AT+PWD=NULL", 1.3),
    ]:
        send(cmd, w)

    for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+PWD=?"):
        send(q, 1.3)

    print("\n>>> HOLD ACT 1-3s to trigger uplink (HiveMQ :1883 probe) <<<\n", flush=True)
    log("TEST", "LISTEN_UPLINK 150s — press ACT")
    deadline = time.time() + 150
    opened = connected = upload_ok = failed = False
    markers: list[str] = []
    while time.time() < deadline:
        for L in read_lines(1.0):
            if "Opened the MQTT" in L:
                opened = True
                markers.append(L)
            if "Successfully connected to the server" in L:
                connected = True
                markers.append(L)
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                upload_ok = True
                markers.append(L)
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                failed = True
                markers.append(L)
                log("MARK", "FAILED_SEND")
            if "not authori" in L.lower():
                markers.append(L)
                log("MARK", "AUTH")
        if connected or upload_ok:
            # collect a few more lines then stop early
            read_lines(8.0)
            break

    log("TEST", f"PROBE opened={opened} connected={connected} upload={upload_ok} failed={failed}")

    # Restore Railway endpoint + auth (leave topics/client/PRO alone)
    print("\n>>> HOLD ACT if needed to restore Railway <<<\n", flush=True)
    if not wake_unlock(90.0):
        log("TEST", "RESTORE_UNLOCK_FAIL — device may still be on HiveMQ")
    else:
        log("TEST", "RESTORE_RAILWAY")
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
    print(f"opened={opened} connected={connected} upload_ok={upload_ok} failed_send={failed}", flush=True)
    print(f"markers={markers[-12:]}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)
    ser.close()
    raise SystemExit(0 if connected or upload_ok else 1)


if __name__ == "__main__":
    main()
