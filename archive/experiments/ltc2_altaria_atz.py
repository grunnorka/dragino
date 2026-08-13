#!/usr/bin/env python3
"""LTC2 -> altaria 66.33.22.220:33239 with ATZ so BKDNS takes effect.

Warm-window: set SERVADDR/BKDNS/auth/TDC60, ATZ, wait reboot wake,
immediately re-pin SERVADDR/BKDNS, then listen-only for uplink cycles.
"""
from __future__ import annotations

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
PASS = "DrgN0-MqTt-7kR9wX2pL"
envp = ROOT / "railway-mqtt.local.env"
if envp.is_file():
    for raw in envp.read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            PASS = raw.split("=", 1)[1].strip()


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_altaria_atz.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; ATZ path {IP},{PORT}; log={logpath}", flush=True)
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

    def send(cmd: str, wait: float = 0.8) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def q_has(cmd: str, needle: str) -> str:
        send(PIN, 0.2)
        got = ""
        for L in send(cmd, 1.0):
            t = L.strip()
            if needle in t:
                return t
            if (
                t
                and t != "OK"
                and not t.startswith("[")
                and not t.startswith("AT+")
                and "Password" not in t
                and "Failed" not in t
                and "Attention" not in t
                and "MQTT" not in t
            ):
                got = t
        return got

    def apply_all() -> None:
        send(PIN, 0.35)
        for cmd in (
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={PASS}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            "AT+MQOS=1",
            "AT+TDC=60",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
        ):
            send(PIN, 0.15)
            send(cmd, 0.7)

    def verify() -> dict[str, str]:
        cfg = {
            "SERVADDR": q_has("AT+SERVADDR=?", IP),
            "BKDNS": q_has("AT+BKDNS=?", IP),
            "TDC": q_has("AT+TDC=?", "60"),
            "CLIENT": q_has("AT+CLIENT=?", CLIENT),
            "PRO": q_has("AT+PRO=?", "3,5"),
            "PUBTOPIC": q_has("AT+PUBTOPIC=?", PUB),
            "UNAME": q_has("AT+UNAME=?", USER),
        }
        # CFG dump fill
        send(PIN, 0.2)
        for L in send("AT+CFG", 5.0):
            if L.startswith("AT+") and "=" in L:
                k, _, v = L.partition("=")
                cfg[k.replace("AT+", "")] = v
        for k, v in cfg.items():
            log("CFG", f"{k}={v}")
        return cfg

    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "cycles": 0,
        "domain": "",
        "atz_done": False,
        "serv_ok": False,
    }
    cfg: dict[str, str] = {}

    # Wait for any warm signal
    log("TEST", "WAIT_WARM for ATZ sequence")
    warm_deadline = time.time() + 900
    warmed = False
    while time.time() < warm_deadline and not warmed:
        for L in read_lines(1.0):
            if any(
                x in L
                for x in (
                    "Password timeout",
                    "Password Correct",
                    "RDY",
                    "Signal Strength",
                    "End of upload",
                    "Upload start",
                    "Failed to send",
                    "Echo mode",
                )
            ):
                # if mid-upload, wait for end first
                if "Upload start" in L or "Opened the MQTT" in L:
                    log("TEST", "mid-upload — wait end before ATZ")
                    while time.time() < warm_deadline:
                        more = read_lines(1.0)
                        if any("End of upload" in x or "NB module power-off" in x for x in more):
                            break
                warmed = True
                break
        if not warmed and time.time() % 12 < 1.2:
            send(PIN, 0.4)
            if any("Password Correct" in L or "LTC2-CB" in L for L in send("AT+MODEL=?", 0.7)):
                warmed = True

    if not warmed:
        log("TEST", "NO_WARM")
        ser.close()
        raise SystemExit(2)

    send(PIN, 0.5)
    apply_all()
    cfg = verify()
    log("TEST", f"PRE_ATZ SERVADDR={cfg.get('SERVADDR')} BKDNS={cfg.get('BKDNS')} TDC={cfg.get('TDC')}")

    log("TEST", "ATZ now")
    send("ATZ", 1.0)
    flags["atz_done"] = True
    # reboot — wait for UART wake
    log("TEST", "WAIT_REBOOT_WAKE")
    wake_deadline = time.time() + 300
    woke = False
    while time.time() < wake_deadline and not woke:
        for L in read_lines(1.0):
            if any(
                x in L
                for x in ("Password timeout", "RDY", "Echo mode", "Signal Strength", "Upload start")
            ):
                woke = True
                break
        if not woke:
            send(PIN, 0.5)
            if any("Password Correct" in L or "LTC2-CB" in L for L in send("AT+MODEL=?", 0.7)):
                woke = True

    if not woke:
        log("TEST", "REBOOT_WAKE_FAIL")
        ser.close()
        raise SystemExit(3)

    # Critical: re-pin immediately after wake (PRO/ATZ may rewrite broker)
    send(PIN, 0.45)
    apply_all()
    cfg = verify()
    flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")
    log("TEST", f"POST_ATZ_APPLY serv_ok={flags['serv_ok']} cfg_serv={cfg.get('SERVADDR')} bk={cfg.get('BKDNS')}")

    # If SERVADDR still empty, hammer it
    if not flags["serv_ok"]:
        for _ in range(6):
            send(PIN, 0.2)
            send(f"AT+SERVADDR={IP},{PORT}", 0.7)
            send(PIN, 0.15)
            send(f"AT+BKDNS=1,0,{IP},{PORT}", 0.7)
        cfg = verify()
        flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")
        log("TEST", f"HAMMER serv_ok={flags['serv_ok']} SERVADDR={cfg.get('SERVADDR')}")

    # Listen-only for up to 3 cycles
    log("TEST", "LISTEN_ONLY after ATZ")
    end = time.time() + 400
    in_upload = False
    while time.time() < end and flags["upload"] == 0:
        for L in read_lines(1.0):
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
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected to the server" in L:
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
                # re-assert address for next cycle
                send(PIN, 0.35)
                send(f"AT+SERVADDR={IP},{PORT}", 0.7)
                send(PIN, 0.15)
                send(f"AT+BKDNS=1,0,{IP},{PORT}", 0.7)
                cfg["SERVADDR"] = q_has("AT+SERVADDR=?", IP)
                cfg["BKDNS"] = q_has("AT+BKDNS=?", IP)
                flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")
                log("TEST", f"REASSERT serv_ok={flags['serv_ok']} SERVADDR={cfg.get('SERVADDR')} BKDNS={cfg.get('BKDNS')}")
        if flags["failed"] >= 3 and flags["cycles"] >= 3:
            send(PIN, 0.4)
            for cmd in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+CSQ=?", "AT+CGPADDR=?", "AT+CIMI=?"):
                send(cmd, 1.0)
            break
        _ = in_upload

    print("=== SUMMARY ===", flush=True)
    print(f"target={IP},{PORT}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["upload"] or flags["connected"] else 1)


if __name__ == "__main__":
    main()
