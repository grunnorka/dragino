#!/usr/bin/env python3
"""Listen through post-boot attach (no TX), then quiet CFG+APN fix+Railway restore."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
APN = "iot.1nce.net"
RAIL_IP, RAIL_PORT = "66.33.22.220", "33239"
PASS = "DrgN0-MqTt-7kR9wX2pL"
for raw in (ROOT / "railway-mqtt.local.env").read_text(encoding="utf-8").splitlines():
    if raw.startswith("MQTT_PASS="):
        PASS = raw.split("=", 1)[1].strip().strip('"')


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_listen_cfg_apn.raw.log"
    ser = serial.Serial("COM8", 9600, timeout=0.25, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    print(f"Opened COM8; log={logpath}", flush=True)
    buf = b""
    info: dict[str, str] = {}
    counts = {
        "opened": 0,
        "connected": 0,
        "failed": 0,
        "mqtt_cfg": 0,
        "pdp_fail": 0,
        "dns_fail": 0,
    }

    def log(tag: str, s: str) -> None:
        safe = s.replace(PASS, "***").replace(PIN, "***PIN***")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(sec: float) -> list[str]:
        nonlocal buf
        end = time.time() + sec
        out: list[str] = []
        while time.time() < end:
            c = ser.read(4096)
            if c:
                buf += c
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    t = line.decode("utf-8", "replace").rstrip("\r")
                    if t:
                        log("RX", t)
                        out.append(t)
            else:
                time.sleep(0.02)
        return out

    def send(cmd: str, w: float = 1.0) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode())
        ser.flush()
        return read_lines(w)

    def note(lines: list[str]) -> None:
        for L in lines:
            if "IMSI" in L:
                info["imsi"] = L
            if "Network Information" in L:
                info["net"] = L
            if "Domain IP" in L or "No DNS" in L:
                info["domain"] = L
            if "Opened the MQTT" in L:
                counts["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected" in L:
                counts["connected"] += 1
                log("MARK", "CONNECTED")
            if "Failed to send" in L:
                counts["failed"] += 1
                log("MARK", "FAILED_SEND")
            if "MQTT configuration failed" in L:
                counts["mqtt_cfg"] += 1
                log("MARK", "MQTT_CFG_FAIL")
            if "Failed to activate PDP" in L:
                counts["pdp_fail"] += 1
            if "DNS configuration failed" in L:
                counts["dns_fail"] += 1
            if L.startswith("AT+APN="):
                info["apn"] = L.split("=", 1)[1].strip()
            if L.startswith("AT+SERVADDR="):
                info["serv"] = L.split("=", 1)[1].strip()

    # Phase A: listen-only until one full attach+upload ends (or timeout)
    log("TEST", "LISTEN_ONLY through boot/attach/upload")
    end = time.time() + 240
    saw_upload = False
    while time.time() < end:
        lines = read_lines(1.0)
        note(lines)
        if any("Upload start" in L for L in lines):
            saw_upload = True
        if saw_upload and any("power-off successful" in L for L in lines):
            log("TEST", "cycle done")
            read_lines(2.0)
            break

    log("TEST", f"ATTACH_INFO {info} counts={counts}")

    # Phase B: unlock + CFG (modem off)
    log("TEST", "UNLOCK+CFG")
    unlocked = False
    for _ in range(15):
        r = send(PIN, 1.2)
        note(r)
        if any("Password Correct" in x for x in r):
            unlocked = True
            break
    if not unlocked:
        log("TEST", "FAIL unlock")
        ser.close()
        return 2

    lines = send("AT+CFG", 12.0)
    note(lines)
    for q in ("AT+APN=?", "AT+SERVADDR=?", "AT+BKDNS=?", "AT+PRO=?", "AT+CLIENT=?", "AT+UNAME=?", "AT+TDC=?"):
        for L in send(q, 1.5):
            note([L])
            t = L.strip()
            if t and t != "OK" and not t.startswith("[") and "Password" not in t:
                if "APN" in q and "apn" not in info:
                    info["apn"] = t
                if "SERVADDR" in q and "serv" not in info:
                    info["serv"] = t
    log("TEST", f"CFG_INFO {info}")

    # Phase C: force 1NCE APN + Railway restore (no ATZ unless APN wrong)
    log("TEST", "SET APN+Railway")
    for cmd, w in [
        (f"AT+APN={APN}", 1.2),
        (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.8),
        (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 0.8),
        ("AT+UNAME=dragino", 0.5),
        (f"AT+PWD={PASS}", 0.5),
        ("AT+PRO=3,5", 0.5),
        ("AT+TLSMOD=0,0", 0.5),
        ("AT+CLIENT=ltc2", 0.5),
        ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
        ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
        ("AT+TDC=60", 0.6),
        (f"AT+APN={APN}", 0.8),
        (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.8),
    ]:
        note(send(cmd, w))

    lines = send("AT+CFG", 12.0)
    note(lines)
    info2 = dict(info)
    for L in lines:
        if L.startswith("AT+APN="):
            info2["apn"] = L.split("=", 1)[1].strip()
        if L.startswith("AT+SERVADDR="):
            info2["serv"] = L.split("=", 1)[1].strip()
    for L in send("AT+APN=?", 1.5):
        t = L.strip()
        if t and t != "OK" and not t.startswith("["):
            info2["apn"] = t
    for L in send("AT+SERVADDR=?", 1.5):
        t = L.strip()
        if t and t != "OK" and not t.startswith("[") and "Password" not in t:
            info2["serv"] = t
    log("TEST", f"POST_SET {info2}")

    # If APN still not 1nce, ATZ once more when quiet
    need_atz = APN not in info2.get("apn", "")
    if need_atz:
        log("TEST", "APN not sticky — ATZ")
        note(send(f"AT+APN={APN}", 1.2))
        note(send("ATZ", 2.0))
        # wait boot without TX until Echo/Signal (skip lone AT)
        end = time.time() + 200
        while time.time() < end:
            lines = read_lines(1.0)
            note(lines)
            if any(
                "Echo mode" in L or "Signal Strength" in L or "Set APN successfully" in L or "NB-IoT Stack" in L
                for L in lines
            ):
                break
            if any(L.strip() == "AT" for L in lines):
                continue
        # wait one upload or power-off
        end = time.time() + 180
        while time.time() < end:
            lines = read_lines(1.0)
            note(lines)
            if any("power-off successful" in L for L in lines):
                break
        # unlock repin railway
        for _ in range(12):
            r = send(PIN, 1.1)
            note(r)
            if any("Password Correct" in x for x in r):
                break
        for cmd, w in [
            (f"AT+APN={APN}", 1.0),
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.8),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 0.8),
            ("AT+UNAME=dragino", 0.5),
            (f"AT+PWD={PASS}", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PRO=3,5", 0.5),
            ("AT+TLSMOD=0,0", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
        ]:
            note(send(cmd, w))
        for L in send("AT+APN=?", 1.5):
            t = L.strip()
            if t and t != "OK":
                info2["apn"] = t
                log("CFG", f"APN={t}")
        for L in send("AT+SERVADDR=?", 1.5):
            t = L.strip()
            if t and t != "OK":
                info2["serv"] = t
                log("CFG", f"SERV={t}")

    # Listen 2 more cycles for CONNECT
    log("TEST", "LISTEN 2 cycles")
    cycles = 0
    end = time.time() + 200
    while time.time() < end and cycles < 2:
        lines = read_lines(1.0)
        note(lines)
        if any("Upload start" in L for L in lines):
            cycles += 1
            log("MARK", f"CYCLE {cycles}")

    log("TEST", f"FINAL info={info2} counts={counts}")
    print(f"LOG={logpath}", flush=True)
    ser.close()
    return 0 if counts["connected"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
