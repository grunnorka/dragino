#!/usr/bin/env python3
"""Carrier proof: prepin LTC2 to test.mosquitto.org:1883 in RDY window, listen, restore Railway.

Pattern from ltc2_altaria_prepin.py — no AT spam during MQTT dial.
"""
from __future__ import annotations

import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
MOSQ_HOST = "test.mosquitto.org"
MOSQ_PORT = "1883"
RAIL_IP, RAIL_PORT = "66.33.22.220", "33239"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
PASS = "DrgN0-MqTt-7kR9wX2pL"
envp = ROOT / "railway-mqtt.local.env"
if envp.is_file():
    for raw in envp.read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            PASS = raw.split("=", 1)[1].strip()
        if raw.startswith("MQTT_FALLBACK_IP="):
            RAIL_IP = raw.split("=", 1)[1].strip()


def resolve_ip(host: str) -> str:
    for fam, _, _, _, sockaddr in socket.getaddrinfo(host, int(MOSQ_PORT), type=socket.SOCK_STREAM):
        if fam == socket.AF_INET:
            return sockaddr[0]
    return socket.getaddrinfo(host, int(MOSQ_PORT), type=socket.SOCK_STREAM)[0][4][0]


def main() -> None:
    mosq_ip = resolve_ip(MOSQ_HOST)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_mosqorg1883_prepin.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; proof {mosq_ip},{MOSQ_PORT}; log={logpath}", flush=True)
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

    def send(cmd: str, wait: float = 0.7) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def prepin_mosq() -> dict[str, str]:
        log("TEST", f"PREPIN_MOSQ {mosq_ip},{MOSQ_PORT}")
        send(PIN, 0.35)
        for _ in range(4):
            send(f"AT+SERVADDR={mosq_ip},{MOSQ_PORT}", 0.55)
            send(PIN, 0.15)
            send(f"AT+BKDNS=1,0,{mosq_ip},{MOSQ_PORT}", 0.55)
            send(PIN, 0.15)
        for cmd in (
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+CLIENT={CLIENT}",
            "AT+UNAME=NULL",
            "AT+PWD=NULL",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            "AT+TDC=60",
            f"AT+SERVADDR={mosq_ip},{MOSQ_PORT}",
            f"AT+BKDNS=1,0,{mosq_ip},{MOSQ_PORT}",
        ):
            send(PIN, 0.12)
            send(cmd, 0.5)
        send(PIN, 0.15)
        serv = ""
        for L in send("AT+SERVADDR=?", 0.9):
            t = L.strip()
            if mosq_ip in t and MOSQ_PORT in t:
                serv = t
                break
            if t and t != "OK" and not t.startswith("[") and "," in t and "Password" not in t:
                serv = t
        send(PIN, 0.15)
        bk = ""
        for L in send("AT+BKDNS=?", 0.9):
            t = L.strip()
            if t.startswith("1,") or mosq_ip in t:
                bk = t
                break
        cfg = {"SERVADDR": serv, "BKDNS": bk}
        ok = mosq_ip in serv and MOSQ_PORT in serv
        log("CFG", f"SERVADDR={serv} BKDNS={bk} ok={ok}")
        return cfg

    def restore_railway() -> None:
        log("TEST", "RESTORE_RAILWAY")
        send(PIN, 0.35)
        for _ in range(3):
            send(f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.55)
            send(PIN, 0.15)
            send(f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 0.55)
            send(PIN, 0.15)
        for cmd in (
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={PASS}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}",
            f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}",
        ):
            send(PIN, 0.12)
            send(cmd, 0.5)
        for q in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?", "AT+CLIENT=?"):
            send(PIN, 0.12)
            send(q, 0.8)

    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "cycles": 0,
        "domain": "",
        "prepinned": False,
        "serv_ok": False,
        "hivemq": False,
    }
    cfg: dict[str, str] = {}
    in_upload = False
    end = time.time() + 420
    log("TEST", "WAIT RDY/Signal then PREPIN mosquitto.org:1883; quiet during MQTT")

    while time.time() < end and flags["connected"] == 0:
        for L in read_lines(1.0):
            if (not in_upload) and (
                L.strip() == "RDY"
                or "Signal Strength" in L
                or "Echo mode turned off" in L
            ):
                if not flags["prepinned"] or not flags["serv_ok"]:
                    cfg = prepin_mosq()
                    flags["prepinned"] = True
                    flags["serv_ok"] = mosq_ip in cfg.get("SERVADDR", "") and MOSQ_PORT in cfg.get(
                        "SERVADDR", ""
                    )

            if "Upload start" in L:
                flags["cycles"] += 1
                in_upload = True
                log(
                    "MARK",
                    f"CYCLE n={flags['cycles']} serv_ok={flags['serv_ok']} SERVADDR={cfg.get('SERVADDR')}",
                )
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L[:160]}")
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
                log("MARK", f"FAILED_SEND n={flags['failed']}")
            if "End of upload" in L:
                in_upload = False
                if flags["connected"] == 0 and flags["cycles"] < 4:
                    # re-pin for next cycle only if not proven yet
                    read_lines(2.0)
                    if not in_upload:
                        cfg = prepin_mosq()
                        flags["serv_ok"] = mosq_ip in cfg.get("SERVADDR", "") and MOSQ_PORT in cfg.get(
                            "SERVADDR", ""
                        )
                        log("TEST", f"POST_END_PREPIN serv_ok={flags['serv_ok']}")

        if flags["failed"] >= 6 and flags["connected"] == 0 and flags["cycles"] >= 3:
            log("TEST", "STOP after multi-cycle fail")
            break

    # Quiet linger if connected, to catch upload line
    if flags["connected"]:
        log("TEST", "CONNECTED — linger 45s for upload")
        t = time.time() + 45
        while time.time() < t:
            for L in read_lines(1.0):
                if "Upload data successfully" in L:
                    flags["upload"] += 1
                    log("MARK", "UPLOAD_OK")

    # Restore on next quiet RDY (or force if UART warm)
    log("TEST", "WAIT quiet window for RESTORE")
    restored = False
    t = time.time() + 90
    while time.time() < t and not restored:
        for L in read_lines(1.0):
            if L.strip() == "RDY" or "Signal Strength" in L or "Echo mode turned off" in L:
                restore_railway()
                restored = True
                break
    if not restored:
        restore_railway()

    print("=== SUMMARY ===", flush=True)
    print({"mosq_ip": mosq_ip, "flags": flags, "cfg": cfg, "log": str(logpath)}, flush=True)
    ser.close()
    raise SystemExit(0 if flags["connected"] else 1)


if __name__ == "__main__":
    main()
