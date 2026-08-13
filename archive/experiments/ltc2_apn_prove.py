#!/usr/bin/env python3
"""Quiet-window CFG prove APN, cold ATZ, capture attach APN/PDP, listen CONNECT, restore Railway."""
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


def mqtt_pass() -> str:
    for raw in (ROOT / "railway-mqtt.local.env").read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            return raw.split("=", 1)[1].strip().strip('"')
    raise SystemExit("MQTT_PASS missing")


def mosq() -> str:
    for fam, _, _, _, sa in socket.getaddrinfo("test.mosquitto.org", 1883, type=socket.SOCK_STREAM):
        if fam == socket.AF_INET:
            return sa[0]
    return "54.36.178.49"


def main() -> int:
    pw = mqtt_pass()
    mip = mosq()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_apn_prove.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial("COM8", 9600, timeout=0.25, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    print(f"Opened COM8; prove APN; log={logpath}", flush=True)
    buf = b""
    state = {
        "apn": "",
        "serv": "",
        "bkdns": "",
        "pro": "",
        "client": "",
        "tdc": "",
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "mqtt_cfg_fail": 0,
        "pdp_fail": 0,
        "dns_fail": 0,
        "imsi": "",
        "netinfo": "",
        "domain": "",
        "boot": False,
    }

    def log(tag: str, s: str) -> None:
        safe = s.replace(pw, "***").replace(PIN, "***PIN***")
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
                state["imsi"] = L
            if "Network Information" in L:
                state["netinfo"] = L
            if "Domain IP" in L or "No DNS" in L:
                state["domain"] = L
            if "Opened the MQTT" in L:
                state["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected" in L:
                state["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                state["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "Failed to open the MQTT" in L:
                state["open_fail"] += 1
            if "Failed to send" in L:
                state["failed"] += 1
                log("MARK", "FAILED_SEND")
            if "MQTT configuration failed" in L:
                state["mqtt_cfg_fail"] += 1
                log("MARK", "MQTT_CFG_FAIL")
            if "Failed to activate PDP" in L:
                state["pdp_fail"] += 1
            if "DNS configuration failed" in L:
                state["dns_fail"] += 1
            if "bootloader" in L.lower() or "NB-IoT Stack" in L:
                state["boot"] = True
                log("MARK", "BOOT")

    def unlock() -> bool:
        for _ in range(12):
            r = send(PIN, 1.2)
            note(r)
            if any("Password Correct" in x for x in r):
                return True
        return False

    def wait_power_off(max_s: float = 180.0) -> bool:
        log("TEST", "WAIT power-off")
        end = time.time() + max_s
        while time.time() < end:
            lines = read_lines(1.0)
            note(lines)
            if any("power-off successful" in L for L in lines):
                read_lines(2.5)
                return True
        return False

    def parse_cfg(lines: list[str]) -> None:
        for L in lines:
            if L.startswith("AT+APN="):
                state["apn"] = L.split("=", 1)[1].strip()
            elif L.startswith("AT+SERVADDR="):
                state["serv"] = L.split("=", 1)[1].strip()
            elif L.startswith("AT+BKDNS="):
                state["bkdns"] = L.split("=", 1)[1].strip()
            elif L.startswith("AT+PRO="):
                state["pro"] = L.split("=", 1)[1].strip()
            elif L.startswith("AT+CLIENT="):
                state["client"] = L.split("=", 1)[1].strip()
            elif L.startswith("AT+TDC="):
                state["tdc"] = L.split("=", 1)[1].strip()

    def cfg_dump(tag: str) -> None:
        log("TEST", f"CFG_DUMP {tag}")
        # one unlock, then CFG with long read — do not re-PIN mid-dump
        send(PIN, 0.5)
        lines = send("AT+CFG", 10.0)
        note(lines)
        parse_cfg(lines)
        # dedicated reads
        for q, key in (
            ("AT+APN=?", "apn"),
            ("AT+SERVADDR=?", "serv"),
            ("AT+BKDNS=?", "bkdns"),
            ("AT+PRO=?", "pro"),
            ("AT+CLIENT=?", "client"),
            ("AT+TDC=?", "tdc"),
        ):
            for L in send(q, 1.4):
                note([L])
                t = L.strip()
                if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                    continue
                if "Password" in t or "Attention" in t or "Upload" in t:
                    continue
                if not state[key]:
                    state[key] = t
        log(
            "TEST",
            f"PARSED {tag} APN={state['apn']!r} SERV={state['serv']!r} "
            f"BKDNS={state['bkdns']!r} PRO={state['pro']!r} CLIENT={state['client']!r} TDC={state['tdc']!r}",
        )

    def apply_target(kind: str) -> None:
        if kind == "mosq":
            ip, port, u, p = mip, "1883", "NULL", "NULL"
        else:
            ip, port, u, p = RAIL_IP, RAIL_PORT, "dragino", pw
        log("TEST", f"APPLY {kind} APN={APN} {ip}:{port}")
        for cmd, w in [
            (f"AT+APN={APN}", 1.3),
            (f"AT+TDC={TDC}", 0.8),
            ("AT+PRO=3,5", 0.7),
            ("AT+TLSMOD=0,0", 0.5),
            (f"AT+SERVADDR={ip},{port}", 0.8),
            (f"AT+BKDNS=1,0,{ip},{port}", 0.8),
            (f"AT+UNAME={u}", 0.5),
            (f"AT+PWD={p}", 0.5),
            ("AT+CLIENT=ltc2", 0.5),
            ("AT+PUBTOPIC=dragino/ltc2/up", 0.5),
            ("AT+SUBTOPIC=dragino/ltc2/down", 0.5),
            ("AT+MQOS=1", 0.5),
            (f"AT+APN={APN}", 0.8),
            (f"AT+SERVADDR={ip},{port}", 0.8),
        ]:
            note(send(cmd, w))

    # 1) quiet unlock + CFG
    if not wait_power_off(200):
        log("TEST", "WARN no power-off; try Echo unlock")
    if not unlock():
        log("TEST", "FAIL unlock pre")
        ser.close()
        return 2
    cfg_dump("before")
    apply_target("mosq")
    cfg_dump("after_set")

    # 2) cold ATZ while still quiet (modem should be off)
    log("TEST", "ATZ cold")
    note(send("ATZ", 2.0))

    # 3) wait boot + first attach (listen-only through first upload attempts)
    end = time.time() + 240
    saw_set_apn = False
    while time.time() < end:
        lines = read_lines(1.0)
        note(lines)
        if any("Set APN successfully" in L for L in lines):
            saw_set_apn = True
            log("MARK", "SET_APN_OK")
        if any("Upload start" in L for L in lines):
            # listen through this cycle without TX
            while time.time() < end:
                x = read_lines(1.0)
                note(x)
                if any("power-off successful" in L for L in x):
                    break
            break

    log("TEST", f"FIRST_ATTACH saw_set_apn={saw_set_apn} state={state}")

    # 4) post-cycle unlock, re-pin SERVADDR (HiveMQ rewrite), confirm APN, listen more
    if unlock():
        apply_target("mosq")
        cfg_dump("post_atz_repin")

    end = time.time() + 300
    in_up = False
    while time.time() < end and state["connected"] < 1:
        lines = read_lines(1.0)
        note(lines)
        if any("Upload start" in L for L in lines):
            in_up = True
        if any("End of upload" in L or "power-off" in L for L in lines):
            in_up = False
        if in_up:
            continue
        if any(L.strip() == "RDY" or "Echo mode" in L for L in lines):
            if unlock():
                note(send(f"AT+APN={APN}", 0.7))
                note(send(f"AT+SERVADDR={mip},1883", 0.7))

    log("TEST", f"MOSQ_PROOF {state}")

    # 5) restore Railway + keep APN
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
        if any(L.strip() == "RDY" or "Echo mode" in L or "Signal Strength" in L for L in lines):
            if unlock():
                apply_target("rail")
                cfg_dump("restored")
                restored = True

    log("TEST", f"FINAL {state} restored={restored}")
    print(f"LOG={logpath}", flush=True)
    ser.close()
    return 0 if state["connected"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
