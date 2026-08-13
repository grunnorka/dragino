#!/usr/bin/env python3
"""Fix LTC2 1NCE APN mismatch, prove MQTT CONNECT, leave on Railway.

Evidence: LTC2 IMSI 901288920101863 (1NCE) was on APN lpwa.vodafone.is
(PS-CB's Vodafone Iceland SIM APN). Correct 1NCE APN is iot.1nce.net.
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
RAIL_IP, RAIL_PORT = "66.33.22.220", "33239"
RAIL_USER = "dragino"
APN_1NCE = "iot.1nce.net"
APN_OLD = "lpwa.vodafone.is"
TDC = "60"


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


def main() -> int:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    mosq_ip = resolve_ip("test.mosquitto.org")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_apn_1nce_fix.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; APN->{APN_1NCE}; log={logpath}", flush=True)
    buf = b""
    flags = {
        "apn_before": "",
        "apn_after": "",
        "cgpaddr": "",
        "serv": "",
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "domain": "",
        "netinfo": "",
        "imsi": "",
        "boot": False,
        "phase": "wait_quiet",
    }

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

    def qval(cmd: str, wait: float = 1.2) -> str:
        send(PIN, 0.4)
        for L in send(cmd, wait):
            t = L.strip()
            if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                continue
            if "Password" in t or "Attention" in t or "Upload" in t:
                continue
            return t
        return ""

    def unlock_quiet(max_s: float = 240.0) -> bool:
        """Wait for post-upload quiet window, then unlock. Never TX during MQTT."""
        log("TEST", f"WAIT_QUIET unlock up_to={max_s}s")
        end = time.time() + max_s
        in_up = False
        while time.time() < end:
            lines = read_lines(0.8)
            if any("Upload start" in L for L in lines):
                in_up = True
                continue
            if any("End of upload" in L or "power-off" in L for L in lines):
                in_up = False
                # brief settle after modem off
                read_lines(1.5)
                continue
            if in_up:
                continue
            if any(
                L.strip() == "RDY"
                or "Signal Strength" in L
                or "Echo mode" in L
                or "Password Correct" in L
                for L in lines
            ):
                if any("Password Correct" in L for L in lines):
                    return True
                r = send(PIN, 1.5)
                if any("Password Correct" in x for x in r):
                    return True
                if any("Password Correct" in x for x in read_lines(0.8)):
                    return True
            else:
                # light probe only when quiet
                if int(time.time()) % 8 == 0:
                    r = send(PIN, 0.9)
                    if any("Password Correct" in x for x in r):
                        return True
        return False

    def dump_diag(tag: str) -> None:
        log("TEST", f"DIAG {tag}")
        for cmd in (
            "AT+MODEL=?",
            "AT+APN=?",
            "AT+SERVADDR=?",
            "AT+BKDNS=?",
            "AT+PRO=?",
            "AT+CLIENT=?",
            "AT+UNAME=?",
            "AT+PUBTOPIC=?",
            "AT+TLSMOD=?",
            "AT+TDC=?",
            "AT+CSQ=?",
            "AT+CGPADDR=?",
            "AT+CEREG?",
            "AT+COPS?",
        ):
            val = qval(cmd, 1.1)
            log("CFG", f"{cmd} -> {val}")
            if "APN" in cmd:
                if tag == "before":
                    flags["apn_before"] = val
                else:
                    flags["apn_after"] = val
            if "SERVADDR" in cmd:
                flags["serv"] = val
            if "CGPADDR" in cmd:
                flags["cgpaddr"] = val

    def apply_apn_and_target(use_mosq: bool) -> None:
        if use_mosq:
            ip, port = mosq_ip, "1883"
            uname, pwd = "NULL", "NULL"
            log("TEST", f"APPLY APN={APN_1NCE} + MOSQ {ip}:{port}")
        else:
            ip, port = RAIL_IP, RAIL_PORT
            uname, pwd = RAIL_USER, mqtt_pass
            log("TEST", f"APPLY APN={APN_1NCE} + RAILWAY {ip}:{port}")
        for cmd, w in [
            (f"AT+APN={APN_1NCE}", 1.2),
            (f"AT+TDC={TDC}", 0.7),
            ("AT+PRO=3,5", 0.6),
            ("AT+TLSMOD=0,0", 0.5),
            (f"AT+SERVADDR={ip},{port}", 0.7),
            (f"AT+BKDNS=1,0,{ip},{port}", 0.7),
            (f"AT+UNAME={uname}", 0.5),
            (f"AT+PWD={pwd}", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            ("AT+MQOS=1", 0.5),
            (f"AT+SERVADDR={ip},{port}", 0.7),
            (f"AT+BKDNS=1,0,{ip},{port}", 0.7),
            (f"AT+APN={APN_1NCE}", 0.8),
        ]:
            send(cmd, w)
        dump_diag("after_apply")

    def repin(use_mosq: bool) -> None:
        if use_mosq:
            ip, port = mosq_ip, "1883"
            uname, pwd = "NULL", "NULL"
        else:
            ip, port = RAIL_IP, RAIL_PORT
            uname, pwd = RAIL_USER, mqtt_pass
        log("TEST", f"REPIN {ip}:{port} APN={APN_1NCE}")
        for cmd, w in [
            (f"AT+APN={APN_1NCE}", 0.8),
            (f"AT+SERVADDR={ip},{port}", 0.6),
            (f"AT+BKDNS=1,0,{ip},{port}", 0.6),
            (f"AT+UNAME={uname}", 0.45),
            (f"AT+PWD={pwd}", 0.45),
            ("AT+PRO=3,5", 0.45),
            ("AT+TLSMOD=0,0", 0.4),
            ("AT+CLIENT=ltc2", 0.4),
            (f"AT+TDC={TDC}", 0.5),
            (f"AT+SERVADDR={ip},{port}", 0.6),
        ]:
            send(cmd, w)
        for cmd in ("AT+APN=?", "AT+SERVADDR=?", "AT+BKDNS=?", "AT+UNAME=?"):
            log("CFG", f"repin {cmd} -> {qval(cmd, 0.9)}")

    def note_rx(lines: list[str]) -> None:
        for L in lines:
            if "IMSI" in L:
                flags["imsi"] = L
            if "Network Information" in L:
                flags["netinfo"] = L
            if "Domain IP" in L or "No DNS" in L or "Connecting" in L:
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
            if "bootloader" in L.lower() or "NB-IoT Stack" in L:
                flags["boot"] = True

    # --- Phase 1: quiet unlock + dump + set APN + ATZ ---
    if not unlock_quiet(300):
        log("TEST", "FAIL unlock before APN fix")
        ser.close()
        return 2

    dump_diag("before")
    # Prefer public mosquitto for CONNECT proof (no auth), then restore Railway.
    use_mosq_first = True
    apply_apn_and_target(use_mosq=use_mosq_first)

    log("TEST", "ATZ to apply APN/PDP")
    send("ATZ", 2.0)
    flags["phase"] = "post_atz"

    # Wait boot + Password Correct, then immediate repin (HiveMQ rewrite risk)
    boot_deadline = time.time() + 180
    repinned = False
    while time.time() < boot_deadline and not repinned:
        lines = read_lines(0.7)
        note_rx(lines)
        if any("Password Correct" in L for L in lines):
            repin(use_mosq=use_mosq_first)
            repinned = True
            break
        if any(
            L.strip() == "RDY" or "Signal Strength" in L or "Echo mode" in L for L in lines
        ):
            r = send(PIN, 1.2)
            note_rx(r)
            if any("Password Correct" in x for x in r):
                repin(use_mosq=use_mosq_first)
                repinned = True
                break

    if not repinned:
        log("TEST", "WARN no post-ATZ unlock; continue listen")

    # --- Phase 2: listen for CONNECT (listen-only during uplink) ---
    flags["phase"] = "listen_mosq"
    listen_end = time.time() + 420  # ~7 min / several TDC=60 cycles
    in_up = False
    while time.time() < listen_end and flags["connected"] < 1:
        lines = read_lines(1.0)
        note_rx(lines)
        if any("Upload start" in L for L in lines):
            in_up = True
        if any("End of upload" in L or "power-off" in L for L in lines):
            in_up = False
        if in_up:
            continue
        # Between cycles: if Password Correct / RDY, reassert APN+SERVADDR once
        if any("Password Correct" in L for L in lines) or (
            any(L.strip() == "RDY" or "Echo mode" in L for L in lines)
        ):
            if not any("Password Correct" in L for L in lines):
                r = send(PIN, 1.0)
                note_rx(r)
                if not any("Password Correct" in x for x in r):
                    continue
            # light reassert only
            send(f"AT+APN={APN_1NCE}", 0.6)
            if use_mosq_first:
                send(f"AT+SERVADDR={mosq_ip},1883", 0.6)
            else:
                send(f"AT+SERVADDR={RAIL_IP},{RAIL_PORT}", 0.6)

    log("TEST", f"MOSQ_PROOF flags={flags}")

    # --- Phase 3: restore Railway + keep 1NCE APN; listen again if mosq worked ---
    flags["phase"] = "restore_railway"
    # Wait quiet then restore
    restore_ok = False
    end = time.time() + 180
    in_up = False
    while time.time() < end and not restore_ok:
        lines = read_lines(0.8)
        note_rx(lines)
        if any("Upload start" in L for L in lines):
            in_up = True
            continue
        if any("End of upload" in L or "power-off" in L for L in lines):
            in_up = False
            continue
        if in_up:
            continue
        if any("Password Correct" in L for L in lines) or any(
            L.strip() == "RDY" or "Signal Strength" in L or "Echo mode" in L for L in lines
        ):
            if not any("Password Correct" in L for L in lines):
                r = send(PIN, 1.4)
                note_rx(r)
                if not any("Password Correct" in x for x in r):
                    continue
            apply_apn_and_target(use_mosq=False)
            restore_ok = True

    if not restore_ok:
        log("TEST", "WARN railway restore window missed")

    # If mosq CONNECT succeeded, listen one Railway cycle for proof on real target
    if flags["connected"] > 0:
        flags["phase"] = "listen_railway"
        rail_connected_before = flags["connected"]
        listen_end = time.time() + 300
        in_up = False
        while time.time() < listen_end and flags["connected"] == rail_connected_before:
            lines = read_lines(1.0)
            note_rx(lines)
            if any("Upload start" in L for L in lines):
                in_up = True
            if any("End of upload" in L or "power-off" in L for L in lines):
                in_up = False

    log("TEST", f"FINAL flags={flags}")
    print(f"LOG={logpath}", flush=True)
    ser.close()
    return 0 if flags["connected"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
