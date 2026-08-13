#!/usr/bin/env python3
"""Apply 1NCE APN on LTC2 only after modem power-off, then ATZ for real reboot.

Prior run showed ATZ mid-session was ignored (upload counters never reset).
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
APN = "iot.1nce.net"
TDC = "60"


def load_env() -> str:
    path = ROOT / "railway-mqtt.local.env"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ["MQTT_PASS"].strip()


def resolve_mosq() -> str:
    for fam, _, _, _, sa in socket.getaddrinfo("test.mosquitto.org", 1883, type=socket.SOCK_STREAM):
        if fam == socket.AF_INET:
            return sa[0]
    return "54.36.178.49"


def main() -> int:
    mqtt_pass = load_env()
    mosq = resolve_mosq()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_apn_atz_cold.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; cold-ATZ APN={APN}; MOSQ={mosq}; log={logpath}", flush=True)
    buf = b""
    flags = {
        "booted": False,
        "apn_cfg": "",
        "serv_cfg": "",
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "imsi": "",
        "netinfo": "",
        "cgpaddr": "",
        "domain": "",
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

    def note(lines: list[str]) -> None:
        for L in lines:
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
            if "NB-IoT Stack" in L or "bootloader" in L.lower():
                flags["booted"] = True
                log("MARK", "BOOT")

    def unlock_burst(tries: int = 8) -> bool:
        for _ in range(tries):
            r = send(PIN, 1.0)
            note(r)
            if any("Password Correct" in x for x in r):
                return True
            if any("Password Correct" in x for x in read_lines(0.5)):
                return True
        return False

    def apply_mosq() -> None:
        log("TEST", f"APPLY cold APN={APN} MOSQ={mosq}:1883 TDC={TDC}")
        for cmd, w in [
            (f"AT+APN={APN}", 1.3),
            (f"AT+TDC={TDC}", 0.8),
            ("AT+PRO=3,5", 0.7),
            ("AT+TLSMOD=0,0", 0.5),
            (f"AT+SERVADDR={mosq},1883", 0.8),
            (f"AT+BKDNS=1,0,{mosq},1883", 0.8),
            ("AT+UNAME=NULL", 0.5),
            ("AT+PWD=NULL", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            ("AT+MQOS=1", 0.5),
            (f"AT+APN={APN}", 0.8),
            (f"AT+SERVADDR={mosq},1883", 0.8),
            (f"AT+BKDNS=1,0,{mosq},1883", 0.8),
        ]:
            note(send(cmd, w))

    def apply_rail() -> None:
        log("TEST", f"RESTORE Railway {RAIL_IP}:{RAIL_PORT} APN={APN}")
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
            (f"AT+APN={APN}", 0.7),
        ]:
            note(send(cmd, w))

    def cfg_extract() -> None:
        send(PIN, 0.4)
        lines = send("AT+CFG", 7.0)
        note(lines)
        for L in lines:
            if L.startswith("AT+APN="):
                flags["apn_cfg"] = L.split("=", 1)[1].strip()
            if L.startswith("AT+SERVADDR="):
                flags["serv_cfg"] = L.split("=", 1)[1].strip()
            if "CGPADDR" in L or L.startswith("+CGPADDR"):
                flags["cgpaddr"] = L
        log("TEST", f"CFG_EXTRACT APN={flags['apn_cfg']!r} SERV={flags['serv_cfg']!r}")

    # --- wait for modem power-off (quiet) ---
    log("TEST", "WAIT power-off then apply+ATZ")
    deadline = time.time() + 200
    powered_off = False
    while time.time() < deadline and not powered_off:
        lines = read_lines(1.0)
        note(lines)
        if any("power-off successful" in L for L in lines):
            powered_off = True
            log("TEST", "POWER_OFF seen — settle 2s")
            read_lines(2.0)

    if not powered_off:
        # try unlock anyway on Echo/RDY
        log("TEST", "no power-off yet; try Echo/RDY unlock")
        deadline = time.time() + 120
        while time.time() < deadline:
            lines = read_lines(0.8)
            note(lines)
            if any("power-off successful" in L for L in lines):
                powered_off = True
                read_lines(2.0)
                break
            if any("Upload start" in L for L in lines):
                # wait this cycle out
                while time.time() < deadline:
                    x = read_lines(0.8)
                    note(x)
                    if any("power-off successful" in L for L in x):
                        powered_off = True
                        read_lines(2.0)
                        break
                break
            if any(L.strip() == "RDY" or "Echo mode" in L for L in lines):
                if unlock_burst():
                    break

    if not unlock_burst(12):
        log("TEST", "FAIL unlock after power-off")
        ser.close()
        return 2

    apply_mosq()
    cfg_extract()
    if APN not in flags["apn_cfg"]:
        log("TEST", "WARN APN not confirmed in CFG — re-set")
        note(send(f"AT+APN={APN}", 1.2))
        cfg_extract()

    log("TEST", "ATZ now (modem should be off)")
    note(send("ATZ", 2.0))

    # --- wait real boot ---
    boot_deadline = time.time() + 200
    while time.time() < boot_deadline and not flags["booted"]:
        lines = read_lines(0.8)
        note(lines)
        # timestamps resetting / bootloader / stack
        if any("NB module is initializing" in L for L in lines):
            flags["booted"] = True
            log("MARK", "BOOT_INIT")

    # immediate unlock + repin after boot chatter or Password window
    repin_deadline = time.time() + 120
    repinned = False
    while time.time() < repin_deadline and not repinned:
        lines = read_lines(0.7)
        note(lines)
        if any("Password Correct" in L for L in lines):
            apply_mosq()
            cfg_extract()
            repinned = True
            break
        if any(
            L.strip() == "RDY"
            or "Signal Strength" in L
            or "Echo mode" in L
            or "Set APN successfully" in L
            for L in lines
        ):
            if unlock_burst(6):
                apply_mosq()
                cfg_extract()
                repinned = True
                break

    log("TEST", f"POST_BOOT repinned={repinned} booted={flags['booted']} APN={flags['apn_cfg']!r}")

    # --- listen for CONNECT ---
    listen_end = time.time() + 360
    in_up = False
    while time.time() < listen_end and flags["connected"] < 1:
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
                if not unlock_burst(4):
                    continue
            note(send(f"AT+APN={APN}", 0.7))
            note(send(f"AT+SERVADDR={mosq},1883", 0.7))

    log("TEST", f"MOSQ_PROOF {flags}")

    # --- restore Railway, keep APN ---
    restore_deadline = time.time() + 180
    restored = False
    in_up = False
    while time.time() < restore_deadline and not restored:
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
                if not unlock_burst(6):
                    continue
            apply_rail()
            cfg_extract()
            restored = True

    # if CONNECT worked on mosq, wait one railway cycle
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
