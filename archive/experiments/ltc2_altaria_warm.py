#!/usr/bin/env python3
"""LTC2 A/B on PS-CB path (66.33.22.220:33239) with warm-UART timing.

UART only stays up briefly. Strategy:
1) Passive wait for a real uplink window
2) Listen-only during connect/upload (no AT spam)
3) Immediately after End of upload / NB power-off start: unlock + burst-apply
4) Short TDC 60/120, then listen-only for next cycles
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
IP, PORT = "66.33.22.220", "33239"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
WAIT_FIRST_UPLOAD_S = 2400  # cover TDC=1800 leftover
LISTEN_AFTER_S = 900
MAX_FAILS = 5


def load_file_env() -> dict[str, str]:
    out: dict[str, str] = {}
    path = ROOT / "railway-mqtt.local.env"
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    file_env = load_file_env()
    global IP, PORT
    IP = file_env.get("MQTT_FALLBACK_IP", IP).strip()
    PORT = file_env.get("MQTT_PORT", PORT).strip()
    if PORT != "33239":
        IP, PORT = "66.33.22.220", "33239"
    mqtt_pass = file_env["MQTT_PASS"].strip()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_altaria_warm.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; target={IP},{PORT}; log={logpath}", flush=True)
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

    def send(cmd: str, wait: float = 1.2) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def burst_apply() -> dict[str, str]:
        """Fire config as fast as possible while UART is warm."""
        log("TEST", "BURST_APPLY_START")
        send(PIN, 0.5)
        # TDC first
        tdc_set = -1
        for val in (60, 120, 60):
            send(f"AT+TDC={val}", 0.9)
            send(PIN, 0.25)
        # core mqtt
        cmds = [
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={mqtt_pass}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            "AT+MQOS=1",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={mqtt_pass}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+TDC=60",
        ]
        for cmd in cmds:
            send(PIN, 0.2)
            send(cmd, 0.85)

        cfg: dict[str, str] = {}
        # quick reads
        for key, cmd in [
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("PRO", "AT+PRO=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("UNAME", "AT+UNAME=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("TLSMOD", "AT+TLSMOD=?"),
            ("TDC", "AT+TDC=?"),
        ]:
            send(PIN, 0.2)
            for L in send(cmd, 1.1):
                t = L.strip()
                if t and t != "OK" and not t.startswith("AT+") and not t.startswith("["):
                    if not any(
                        x in t
                        for x in (
                            "Password",
                            "Failed",
                            "MQTT",
                            "TCP",
                            "Upload",
                            "Attention",
                            "Closing",
                        )
                    ):
                        cfg[key] = t
                        break
            log("CFG", f"{key}={cfg.get(key, '')}")

        # CFG dump backup
        send(PIN, 0.2)
        for L in send("AT+CFG", 6.0):
            if L.startswith("AT+") and "=" in L:
                k, _, v = L.partition("=")
                cfg[k.replace("AT+", "")] = v
                log("CFG", f"{k.replace('AT+', '')}={v}")

        if cfg.get("TDC", "").isdigit():
            tdc_set = int(cfg["TDC"])
        log("TEST", f"BURST_DONE TDC={tdc_set} SERVADDR={cfg.get('SERVADDR')} BKDNS={cfg.get('BKDNS')}")
        return cfg

    def cfg_ok(cfg: dict[str, str]) -> bool:
        blob = str(cfg).lower()
        return (
            IP in cfg.get("SERVADDR", "")
            and PORT in cfg.get("SERVADDR", "")
            and "3,5" in cfg.get("PRO", "")
            and cfg.get("CLIENT") == CLIENT
            and cfg.get("UNAME") == USER
            and PUB in cfg.get("PUBTOPIC", "")
            and "hivemq" not in blob
            and "24233" not in cfg.get("SERVADDR", "")
        )

    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "cycles": 0,
        "domain": "",
        "auth_fail": 0,
        "applied": False,
        "cfg_ok": False,
    }
    cfg: dict[str, str] = {}
    tdc = -1

    log("TEST", f"PASSIVE_WAIT_UPLOAD up_to={WAIT_FIRST_UPLOAD_S}s")
    # Also probe lightly in case UART already warm
    end_wait = time.time() + WAIT_FIRST_UPLOAD_S
    in_upload = False
    upload_ended = False
    applied = False
    next_probe = time.time() + 10

    while time.time() < end_wait and not (applied and flags["upload"] > 0):
        for L in read_lines(1.0):
            if "Upload start" in L:
                flags["cycles"] += 1
                in_upload = True
                upload_ended = False
                log("MARK", f"CYCLE n={flags['cycles']} LISTEN_ONLY")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L[:140]}")
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Failed to open the MQTT" in L:
                flags["open_fail"] += 1
                log("MARK", "OPEN_FAIL")
            if "Successfully connected to the server" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "not authorised" in L.lower() or "not authorized" in L.lower():
                flags["auth_fail"] += 1
                log("MARK", "AUTH_FAIL")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", f"FAILED_SEND n={flags['failed']}")
            if "End of upload" in L or "NB module power-off" in L:
                in_upload = False
                upload_ended = True
                log("MARK", "UPLOAD_WINDOW_END")
                if not applied:
                    # UART still warm — burst apply NOW
                    send(PIN, 0.45)
                    cfg = burst_apply()
                    applied = True
                    flags["applied"] = True
                    flags["cfg_ok"] = cfg_ok(cfg)
                    if cfg.get("TDC", "").isdigit():
                        tdc = int(cfg["TDC"])
                    log("TEST", f"POST_UPLOAD_APPLY ok={flags['cfg_ok']} cfg={cfg}")
                    # extend listen for short-TDC cycles
                    end_wait = time.time() + LISTEN_AFTER_S
            # Password timeout alone = brief wake — try burst if never applied
            if (not applied) and ("Password timeout" in L or "Password Correct" in L):
                send(PIN, 0.45)
                if any("Password Correct" in x or "LTC2-CB" in x for x in send("AT+MODEL=?", 0.8)):
                    cfg = burst_apply()
                    applied = True
                    flags["applied"] = True
                    flags["cfg_ok"] = cfg_ok(cfg)
                    if cfg.get("TDC", "").isdigit():
                        tdc = int(cfg["TDC"])
                    log("TEST", f"TIMEOUT_WAKE_APPLY ok={flags['cfg_ok']} cfg={cfg}")
                    end_wait = max(end_wait, time.time() + LISTEN_AFTER_S)

        # light probe only when not in upload and not yet applied
        if (not in_upload) and (not applied) and time.time() >= next_probe:
            next_probe = time.time() + 12
            send(PIN, 0.5)
            send("AT+MODEL=?", 0.7)

        if flags["upload"] > 0:
            break
        if applied and flags["failed"] >= MAX_FAILS and flags["upload"] == 0:
            # after apply, still failing — quiet diag once then keep listening a bit
            if flags["failed"] == MAX_FAILS:
                send(PIN, 0.4)
                for cmd in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+CSQ=?", "AT+CGPADDR=?", "AT+TDC=?"):
                    send(cmd, 1.2)
            if flags["failed"] > MAX_FAILS + 2:
                break

    # final verify if warm
    send(PIN, 0.4)
    if any("Password Correct" in L or "LTC2-CB" in L for L in send("AT+MODEL=?", 0.9)):
        for key, cmd in [
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("TDC", "AT+TDC=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("PRO", "AT+PRO=?"),
        ]:
            send(PIN, 0.2)
            for L in send(cmd, 1.1):
                t = L.strip()
                if t and t != "OK" and not t.startswith("[") and not t.startswith("AT+"):
                    if "Password" not in t and "Failed" not in t:
                        cfg[key] = t
                        break

    print("=== SUMMARY ===", flush=True)
    print(f"target={IP},{PORT}", flush=True)
    print(f"tdc={tdc}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"UTC_END={datetime.now(timezone.utc).isoformat()}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["upload"] or flags["connected"] else 1)


if __name__ == "__main__":
    main()
