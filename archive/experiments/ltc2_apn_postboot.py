#!/usr/bin/env python3
"""Post-reboot: wait for stack init, unlock, verify/set 1NCE APN, listen for CONNECT, restore Railway."""
from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
APN = "iot.1nce.net"
RAIL_IP, RAIL_PORT = "66.33.22.220", "33239"
TDC = "60"


def load_pass() -> str:
    for raw in (ROOT / "railway-mqtt.local.env").read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            return raw.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no MQTT_PASS")


def mosq_ip() -> str:
    for fam, _, _, _, sa in socket.getaddrinfo("test.mosquitto.org", 1883, type=socket.SOCK_STREAM):
        if fam == socket.AF_INET:
            return sa[0]
    return "54.36.178.49"


def main() -> int:
    mqtt_pass = load_pass()
    mosq = mosq_ip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_apn_postboot.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial("COM8", 9600, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    print(f"Opened COM8; postboot APN={APN}; log={logpath}", flush=True)
    buf = b""
    flags = {
        "stack": False,
        "imsi": "",
        "apn_line": "",
        "serv": "",
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "netinfo": "",
        "domain": "",
        "cgpaddr": "",
    }

    def log(tag: str, s: str) -> None:
        safe = s.replace(mqtt_pass, "***").replace(PIN, "***PIN***")
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
            if "NB-IoT Stack" in L or "NB module is initializing" in L:
                flags["stack"] = True
                log("MARK", "STACK")
            if "IMSI" in L:
                flags["imsi"] = L
            if "Network Information" in L:
                flags["netinfo"] = L
            if "Domain IP" in L or "No DNS" in L:
                flags["domain"] = L
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "Failed to open the MQTT" in L:
                flags["open_fail"] += 1
                log("MARK", "OPEN_FAIL")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", "FAILED_SEND")

    def unlock() -> bool:
        for _ in range(10):
            r = send(PIN, 1.1)
            note(r)
            if any("Password Correct" in x for x in r):
                return True
        return False

    def set_mosq() -> None:
        log("TEST", f"SET APN={APN} MOSQ={mosq}")
        for cmd, w in [
            (f"AT+APN={APN}", 1.2),
            (f"AT+TDC={TDC}", 0.7),
            ("AT+PRO=3,5", 0.6),
            ("AT+TLSMOD=0,0", 0.5),
            (f"AT+SERVADDR={mosq},1883", 0.8),
            (f"AT+BKDNS=1,0,{mosq},1883", 0.8),
            ("AT+UNAME=NULL", 0.5),
            ("AT+PWD=NULL", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            (f"AT+SERVADDR={mosq},1883", 0.7),
            (f"AT+APN={APN}", 0.7),
        ]:
            note(send(cmd, w))

    def set_rail() -> None:
        log("TEST", "RESTORE_RAILWAY")
        for cmd, w in [
            (f"AT+APN={APN}", 1.0),
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.8),
            (f"AT+BKDNS=1,0,{RAIL_IP},{RAIL_PORT}", 0.8),
            ("AT+UNAME=dragino", 0.5),
            (f"AT+PWD={mqtt_pass}", 0.5),
            ("AT+PRO=3,5", 0.5),
            ("AT+TLSMOD=0,0", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            (f"AT+TDC={TDC}", 0.6),
            (f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.7),
        ]:
            note(send(cmd, w))

    def dump_cfg() -> None:
        send(PIN, 0.35)
        lines = send("AT+CFG", 8.0)
        note(lines)
        for L in lines:
            if L.startswith("AT+APN="):
                flags["apn_line"] = L
            if L.startswith("AT+SERVADDR="):
                flags["serv"] = L
        # also quick queries
        for q in ("AT+APN=?", "AT+SERVADDR=?", "AT+CGPADDR=?", "AT+CSQ=?"):
            send(PIN, 0.3)
            for L in send(q, 1.2):
                note([L])
                if q.startswith("AT+APN") and L.strip() and L.strip() != "OK" and not L.startswith("["):
                    if "APN" not in flags["apn_line"]:
                        flags["apn_line"] = L.strip()
                if "CGPADDR" in q or L.startswith("+CGPADDR") or (
                    L.count(".") == 3 and L[0].isdigit()
                ):
                    if "CGPADDR" in q and L.strip() not in ("OK",) and not L.startswith("AT"):
                        flags["cgpaddr"] = L.strip()
        log("TEST", f"DUMP apn={flags['apn_line']!r} serv={flags['serv']!r} cgp={flags['cgpaddr']!r}")

    # Wait until past bootloader AT spam — look for stack / echo / password window
    log("TEST", "WAIT for post-boot (no TX during bootloader AT spam)")
    end = time.time() + 180
    while time.time() < end:
        lines = read_lines(1.0)
        note(lines)
        if any(
            "NB-IoT Stack" in L
            or "NB module is initializing" in L
            or "Echo mode" in L
            or "Password Correct" in L
            or "Signal Strength" in L
            or "Set APN successfully" in L
            or "Upload start" in L
            for L in lines
        ):
            break
        # ignore lone "AT" bootloader lines

    # If upload already running, wait for power-off
    in_up = any("Upload start" in L for L in lines) if lines else False
    if in_up or any("Opened the MQTT" in L for L in lines):
        log("TEST", "uplink in progress — wait power-off")
        end = time.time() + 120
        while time.time() < end:
            x = read_lines(1.0)
            note(x)
            if any("power-off successful" in L for L in x):
                read_lines(2.0)
                break

    if not unlock():
        # try on next Echo
        end = time.time() + 90
        ok = False
        while time.time() < end and not ok:
            lines = read_lines(0.8)
            note(lines)
            if any("Upload start" in L for L in lines):
                while time.time() < end:
                    x = read_lines(0.8)
                    note(x)
                    if any("power-off" in L for L in x):
                        break
                continue
            if any(L.strip() == "RDY" or "Echo mode" in L or "Signal Strength" in L for L in lines):
                ok = unlock()
        if not ok:
            log("TEST", "FAIL unlock")
            ser.close()
            return 2

    set_mosq()
    dump_cfg()

    # Listen for CONNECT — do not TX during uplink
    log("TEST", "LISTEN mosq CONNECT")
    end = time.time() + 360
    in_up = False
    while time.time() < end and flags["connected"] < 1:
        lines = read_lines(1.0)
        note(lines)
        if any("Upload start" in L for L in lines):
            in_up = True
        if any("End of upload" in L or "power-off" in L for L in lines):
            in_up = False
        if in_up:
            continue
        if any("Password Correct" in L for L in lines) or any(
            L.strip() == "RDY" or "Echo mode" in L for L in lines
        ):
            if not any("Password Correct" in L for L in lines):
                if not unlock():
                    continue
            note(send(f"AT+APN={APN}", 0.7))
            note(send(f"AT+SERVADDR={mosq},1883", 0.7))

    log("TEST", f"MOSQ_PROOF {flags}")

    # Restore Railway
    end = time.time() + 150
    restored = False
    in_up = False
    while time.time() < end and not restored:
        lines = read_lines(0.8)
        note(lines)
        if any("Upload start" in L for L in lines):
            in_up = True
            continue
        if any("End of upload" in L or "power-off" in L for L in lines):
            in_up = False
            continue
        if in_up:
            continue
        if any("Password Correct" in L for L in lines) or any(
            L.strip() == "RDY" or "Echo mode" in L or "Signal Strength" in L for L in lines
        ):
            if not any("Password Correct" in L for L in lines):
                if not unlock():
                    continue
            set_rail()
            dump_cfg()
            restored = True

    if flags["connected"] > 0 and restored:
        before = flags["connected"]
        end = time.time() + 200
        while time.time() < end and flags["connected"] == before:
            note(read_lines(1.0))

    log("TEST", f"FINAL {flags} restored={restored}")
    print(f"LOG={logpath}", flush=True)
    ser.close()
    return 0 if flags["connected"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
