#!/usr/bin/env python3
"""LTC2 carrier proof: dial test.mosquitto.org:1883, wait cycles, restore Railway.

Uses ATZ boot uplink listen so ACT button is not required mid-test.
"""
from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
MOSQ_HOST = "test.mosquitto.org"
MOSQ_PORT = 1883
RAIL_IP, RAIL_PORT = "66.33.22.220", 33239
RAIL_USER = "dragino"
CLIENT = "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_ip(host: str) -> str:
    infos = socket.getaddrinfo(host, MOSQ_PORT, type=socket.SOCK_STREAM)
    for fam, _, _, _, sockaddr in infos:
        if fam == socket.AF_INET:
            return sockaddr[0]
    return infos[0][4][0]


def main() -> None:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    mosq_ip = resolve_ip(MOSQ_HOST)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_mosqorg1883_proof.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; MOSQ={mosq_ip}:{MOSQ_PORT}; log={logpath}", flush=True)
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

    def wake_unlock(timeout: float = 200.0) -> bool:
        log("TEST", f"WAKE_UNLOCK wait up to {timeout}s (quiet RDY window)")
        deadline = time.time() + timeout
        last_try = 0.0
        uploading = False
        while time.time() < deadline:
            lines = read_lines(1.0)
            if any("Upload start" in L for L in lines):
                uploading = True
            if any(
                "Failed to send" in L
                or "Upload data successfully" in L
                or "Successfully connected" in L
                for L in lines
            ):
                uploading = False
            if any("Password Correct" in L for L in lines):
                log("TEST", "UNLOCK_OK")
                return True
            if any("LTC2-CB" in L for L in lines) and not uploading:
                if any("LTC2-CB" in x for x in send("AT+MODEL=?", 1.0)):
                    log("TEST", "UNLOCK_OK already")
                    return True
            quiet_wake = any(
                L.strip() == "RDY" or "Signal Strength" in L for L in lines
            )
            now = time.time()
            if uploading:
                continue
            if quiet_wake or (now - last_try > 12):
                last_try = now
                resp = send(PIN, 1.2)
                if any("Password Correct" in x for x in resp):
                    log("TEST", "UNLOCK_OK")
                    return True
                resp = send("AT+MODEL=?", 1.0)
                if any("LTC2-CB" in x for x in resp):
                    log("TEST", "UNLOCK_OK already")
                    return True
        return False

    if not wake_unlock():
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+PRO=?", "AT+TDC=?", "AT+CLIENT=?"):
        send(q, 1.2)

    log("TEST", f"SET_TEST_MOSQUITTO_1883 {mosq_ip}")
    for cmd, w in [
        (f"AT+SERVADDR={mosq_ip},{MOSQ_PORT}", 1.6),
        (f"AT+BKDNS=1,0,{mosq_ip},{MOSQ_PORT}", 1.6),
        ("AT+UNAME=NULL", 1.2),
        ("AT+PWD=NULL", 1.2),
        ("AT+PRO=3,5", 1.2),
        ("AT+TLSMOD=0,0", 1.2),
        (f"AT+CLIENT={CLIENT}", 1.2),
        (f"AT+PUBTOPIC={PUB}", 1.2),
        (f"AT+SUBTOPIC={SUB}", 1.2),
        (f"AT+SERVADDR={mosq_ip},{MOSQ_PORT}", 1.4),
        (f"AT+BKDNS=1,0,{mosq_ip},{MOSQ_PORT}", 1.4),
    ]:
        send(cmd, w)

    cfg = {}
    for key, q in [
        ("SERVADDR", "AT+SERVADDR=?"),
        ("BKDNS", "AT+BKDNS=?"),
        ("UNAME", "AT+UNAME=?"),
        ("CLIENT", "AT+CLIENT=?"),
        ("PRO", "AT+PRO=?"),
        ("TDC", "AT+TDC=?"),
    ]:
        for L in send(q, 1.2):
            if L.strip() and L.strip() != "OK" and not L.startswith("["):
                cfg[key] = L.strip()
    log("TEST", f"PRE_ATZ cfg={cfg}")

    log("TEST", "ATZ — quiet listen for boot/TDC uplinks")
    send("ATZ", 1.5)

    flags = {
        "boot": False,
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "cycles": 0,
        "domain": "",
        "hivemq": False,
    }
    # Wait ~3 TDC cycles at 60s + boot margin
    end = time.time() + 220
    while time.time() < end:
        for L in read_lines(1.0):
            if "LTC2-CB" in L or L.strip() == "RDY":
                flags["boot"] = True
            if "Upload start" in L:
                flags["cycles"] += 1
                log("MARK", f"CYCLE {flags['cycles']}")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L}")
            if "hivemq" in L.lower():
                flags["hivemq"] = True
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
        if flags["connected"] >= 1 and flags["cycles"] >= 1:
            # got proof; still wait a bit for upload line
            if flags["upload"] or flags["failed"] or flags["cycles"] >= 2:
                break

    log("TEST", f"PROOF_FLAGS {flags}")

    # Restore Railway — try unlock again after boot
    log("TEST", "RESTORE_RAILWAY")
    unlocked = False
    for _ in range(40):
        lines = read_lines(1.0)
        if any("RDY" == L.strip() or "Signal Strength" in L for L in lines):
            if any("Password Correct" in x for x in send(PIN, 0.8)):
                unlocked = True
                break
        if any("Password Correct" in x for x in send(PIN, 0.6)):
            unlocked = True
            break
    if unlocked:
        for cmd, w in [
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 1.5),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 1.5),
            (f"AT+UNAME={RAIL_USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            ("AT+PRO=3,5", 1.2),
            ("AT+TLSMOD=0,0", 1.2),
            (f"AT+CLIENT={CLIENT}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 1.3),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 1.3),
        ]:
            send(cmd, w)
        for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?", "AT+PUBTOPIC=?", "AT+PRO=?"):
            send(q, 1.1)
        log("TEST", "RESTORE_DONE")
    else:
        log("TEST", "RESTORE_UNLOCK_FAIL — device may remain on test.mosquitto.org")

    print("=== SUMMARY ===", flush=True)
    print({"mosq_ip": mosq_ip, "flags": flags, "log": str(logpath)}, flush=True)
    ser.close()
    # exit 0 if Successfully connected observed
    raise SystemExit(0 if flags["connected"] else 1)


if __name__ == "__main__":
    main()
