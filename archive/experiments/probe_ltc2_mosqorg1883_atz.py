#!/usr/bin/env python3
"""ATZ carrier proof: catch Password Correct ASAP, burst-set mosquitto.org:1883, ATZ, listen, restore."""
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


def resolve_ip(host: str) -> str:
    for fam, _, _, _, sockaddr in socket.getaddrinfo(host, 1883, type=socket.SOCK_STREAM):
        if fam == socket.AF_INET:
            return sockaddr[0]
    return socket.getaddrinfo(host, 1883, type=socket.SOCK_STREAM)[0][4][0]


def main() -> None:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    mosq_ip = resolve_ip("test.mosquitto.org")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_mosqorg1883_atz.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; MOSQ={mosq_ip}:1883; log={logpath}", flush=True)
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

    def send(cmd: str, wait: float = 1.0) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def apply_mosq() -> bool:
        log("TEST", f"APPLY_MOSQ {mosq_ip}:1883")
        for cmd, w in [
            (f"AT+SERVADDR={mosq_ip},1883", 0.7),
            (f"AT+BKDNS=1,0,{mosq_ip},1883", 0.7),
            ("AT+UNAME=NULL", 0.5),
            ("AT+PWD=NULL", 0.5),
            ("AT+PRO=3,5", 0.5),
            ("AT+TLSMOD=0,0", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            (f"AT+SERVADDR={mosq_ip},1883", 0.7),
            (f"AT+BKDNS=1,0,{mosq_ip},1883", 0.7),
        ]:
            send(cmd, w)
        serv_ok = False
        for L in send("AT+SERVADDR=?", 0.9):
            if mosq_ip in L and "1883" in L:
                serv_ok = True
        for L in send("AT+BKDNS=?", 0.9):
            log("CFG", f"BKDNS {L}")
        log("TEST", f"SERVADDR_OK={serv_ok}")
        return serv_ok

    def restore_rail() -> None:
        log("TEST", "RESTORE_RAILWAY")
        for cmd, w in [
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.7),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 0.7),
            (f"AT+UNAME={RAIL_USER}", 0.5),
            (f"AT+PWD={mqtt_pass}", 0.5),
            ("AT+PRO=3,5", 0.5),
            ("AT+TLSMOD=0,0", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.7),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 0.7),
        ]:
            send(cmd, w)
        for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?"):
            send(q, 0.8)

    # --- unlock + apply in one pass ---
    log("TEST", "WAIT unlock window")
    deadline = time.time() + 200
    unlocked = False
    applied = False
    in_upload = False
    while time.time() < deadline and not applied:
        lines = read_lines(0.8)
        if any("Upload start" in L for L in lines):
            in_upload = True
        if any("End of upload" in L or "NB module power-off" in L for L in lines):
            in_upload = False

        if any("Password Correct" in L for L in lines):
            unlocked = True
            log("TEST", "UNLOCK_OK")
            applied = apply_mosq()
            break

        if in_upload:
            continue

        wake = any(
            L.strip() == "RDY" or "Signal Strength" in L or "Echo mode turned off" in L
            for L in lines
        )
        if wake or (not unlocked and int(time.time()) % 20 == 0):
            resp = send(PIN, 1.6)
            if any("Password Correct" in x for x in resp):
                unlocked = True
                log("TEST", "UNLOCK_OK")
                applied = apply_mosq()
                break
            # Password Correct may arrive slightly late
            late = read_lines(0.8)
            if any("Password Correct" in x for x in late):
                unlocked = True
                log("TEST", "UNLOCK_OK late")
                applied = apply_mosq()
                break

    if not applied:
        log("TEST", "UNLOCK_OR_APPLY_FAIL")
        ser.close()
        raise SystemExit(2)

    log("TEST", "ATZ")
    send("ATZ", 1.5)

    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "domain": "",
        "netinfo": "",
        "boot": False,
    }
    end = time.time() + 220
    while time.time() < end:
        for L in read_lines(1.0):
            if "LTC2-CB" in L or "Image Version" in L:
                flags["boot"] = True
            if "Network Information" in L:
                flags["netinfo"] = L
            if "Domain IP" in L or "Connecting" in L or "No DNS" in L:
                flags["domain"] = L
                log("MARK", f"NET {L}")
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Failed to open the MQTT" in L:
                flags["open_fail"] += 1
                log("MARK", "OPEN_FAIL")
            if "Successfully connected" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", "FAILED_SEND")
        if flags["connected"] and flags["cycles"] if False else (flags["connected"] and (flags["upload"] or flags["failed"] or flags["opened"])):
            break
        if flags["boot"] and (flags["failed"] + flags["open_fail"]) >= 3 and flags["connected"] == 0:
            # keep listening until module powers off once
            if any("NB module power-off" in L for L in read_lines(0.1)):
                break

    log("TEST", f"PROOF_FLAGS {flags}")

    # restore on next quiet wake
    log("TEST", "WAIT restore window")
    deadline = time.time() + 120
    restored = False
    in_upload = False
    while time.time() < deadline and not restored:
        lines = read_lines(0.8)
        if any("Upload start" in L for L in lines):
            in_upload = True
        if any("End of upload" in L or "NB module power-off" in L for L in lines):
            in_upload = False
        if any("Password Correct" in L for L in lines):
            restore_rail()
            restored = True
            break
        if in_upload:
            continue
        if any(L.strip() == "RDY" or "Signal Strength" in L or "Echo mode turned off" in L for L in lines):
            resp = send(PIN, 1.6)
            if any("Password Correct" in x for x in resp) or any(
                "Password Correct" in x for x in read_lines(0.8)
            ):
                restore_rail()
                restored = True
                break
    if not restored:
        log("TEST", "RESTORE_UNLOCK_FAIL")

    print("=== SUMMARY ===", flush=True)
    print({"mosq_ip": mosq_ip, "flags": flags, "log": str(logpath)}, flush=True)
    ser.close()
    raise SystemExit(0 if flags["connected"] else 1)


if __name__ == "__main__":
    main()
