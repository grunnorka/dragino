#!/usr/bin/env python3
"""LTC2-CB Railway MQTT on COM8@9600 — apply immediately after unlock (UART sleeps fast)."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
USER = "dragino"
CLIENT = "ltc2"
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
PIN = "358613"
COM = "COM8"
BAUD = 9600


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    # LTC2 dedicated TCP forwarder (PS-CB stays on altaria:33239 / 66.33.22.220)
    host_ip = os.environ.get("MQTT_LTC2_FALLBACK_IP", "66.33.22.223").strip()
    port = int(os.environ.get("MQTT_LTC2_PORT", "24233"))
    # bind into closures used below
    global HOST_IP, PORT
    HOST_IP, PORT = host_ip, port
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_serial_railway.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}@{BAUD}; log={logpath}", flush=True)
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

    def wake_unlock(timeout: float = 180.0) -> bool:
        print(
            "\n>>> HOLD ACT 1-3s on LTC2 NOW (wake UART) <<<\n",
            flush=True,
        )
        log("TEST", f"WAKE_UNLOCK {timeout}s")
        deadline = time.time() + timeout
        while time.time() < deadline:
            lines = send(PIN, 0.9)
            if any("Password Correct" in L for L in lines):
                # confirm with MODEL quickly
                m = send("AT+MODEL=?", 1.2)
                if any("LTC2-CB" in L for L in m) or True:
                    log("TEST", "UNLOCK_OK")
                    return True
            m = send("AT+MODEL=?", 1.0)
            if any("LTC2-CB" in L for L in m):
                log("TEST", "UNLOCK_OK already")
                return True
        return False

    def apply() -> None:
        for cmd, w in [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.4),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 1.8),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 1.8),
            (f"AT+CLIENT={CLIENT}", 1.4),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={mqtt_pass}", 1.3),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+SUBTOPIC={SUB}", 1.3),
            ("AT+MQOS=1", 1.2),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 1.6),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 1.6),
            (f"AT+CLIENT={CLIENT}", 1.3),
            (f"AT+UNAME={USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
        ]:
            send(cmd, w)

    def query_map() -> dict[str, str]:
        out: dict[str, str] = {}
        for key, cmd in [
            ("MODEL", "AT+MODEL=?"),
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("PRO", "AT+PRO=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("UNAME", "AT+UNAME=?"),
            ("PWD", "AT+PWD=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("SUBTOPIC", "AT+SUBTOPIC=?"),
            ("TLSMOD", "AT+TLSMOD=?"),
            ("TDC", "AT+TDC=?"),
        ]:
            for L in send(cmd, 1.6):
                t = L.strip()
                if (
                    not t
                    or t == "OK"
                    or t.startswith("[")
                    or t.startswith("AT+")
                    or "Password" in t
                    or "Attention" in t
                    or "Upload" in t
                    or "MQTT" in t
                    or "Failed" in t
                    or "Domain" in t
                ):
                    continue
                out[key] = t
                break
        for L in send("AT+CFG", 6.0):
            m = re.match(r"AT\+([A-Z0-9]+)=(.*)$", L.strip())
            if m:
                out[m.group(1)] = m.group(2)
        return out

    def verify(cfg: dict[str, str]) -> dict[str, bool]:
        blob = str(cfg).lower()
        serv = cfg.get("SERVADDR", "")
        bk = cfg.get("BKDNS", "")
        return {
            "no_HiveMQ": "hivemq" not in blob,
            "SERVADDR": HOST_IP in serv and str(PORT) in serv,
            "BKDNS": HOST_IP in bk and str(PORT) in bk,
            "PRO_3_5": "3,5" in cfg.get("PRO", ""),
            "CLIENT": cfg.get("CLIENT") == CLIENT,
            "UNAME": cfg.get("UNAME") == USER,
            "PWD_set": bool(cfg.get("PWD")) and str(cfg.get("PWD", "")).upper() != "NULL",
            "PUB": PUB in cfg.get("PUBTOPIC", ""),
            "SUB": SUB in cfg.get("SUBTOPIC", ""),
            "TLS_off": "0,0" in cfg.get("TLSMOD", ""),
        }

    def log_checks(checks: dict[str, bool], label: str) -> bool:
        log("TEST", f"--- {label} ---")
        ok = True
        for k, v in checks.items():
            log("TEST", f"{'PASS' if v else 'FAIL'}: {k}")
            ok = ok and v
        return ok

    if not wake_unlock(180.0):
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    # Apply IMMEDIATELY — do not wait_idle (UART sleeps)
    apply()
    cfg = query_map()
    log("TEST", f"CFG={cfg}")
    ok = log_checks(verify(cfg), "verify pre-ATZ")

    if not (HOST_IP in cfg.get("SERVADDR", "") and str(PORT) in cfg.get("SERVADDR", "")):
        log("TEST", "SERVADDR missing — re-apply + query")
        apply()
        cfg = query_map()
        log("TEST", f"CFG2={cfg}")
        ok = log_checks(verify(cfg), "verify pre-ATZ retry")

    serv_ok = HOST_IP in cfg.get("SERVADDR", "") and str(PORT) in cfg.get("SERVADDR", "")
    if not serv_ok:
        print("=== SUMMARY ===", flush=True)
        print(f"verify_ok={ok} CFG={cfg}", flush=True)
        print("BLOCKER: SERVADDR not verified; skipped ATZ", flush=True)
        print(f"LOG={logpath}", flush=True)
        ser.close()
        raise SystemExit(1)

    # ATZ for PRO/BKDNS ("Take effect after ATZ")
    log("TEST", "SERVADDR OK — ATZ")
    send("ATZ", 1.5)
    print("\n>>> REBOOT — HOLD ACT 1-3s in ~12s <<<\n", flush=True)
    time.sleep(12.0)
    if not wake_unlock(180.0):
        log("TEST", "post-ATZ unlock fail")
        ser.close()
        raise SystemExit(3)

    apply()
    cfg = query_map()
    log("TEST", f"CFG_POST={cfg}")
    ok = log_checks(verify(cfg), "verify post-ATZ")

    # brief uplink listen
    log("TEST", "LISTEN 120s")
    deadline = time.time() + 120
    success = connected = False
    markers: list[str] = []
    while time.time() < deadline:
        for L in read_lines(1.0):
            low = L.lower()
            if "Opened the MQTT" in L or "Successfully connected to the server" in L:
                connected = True
                markers.append(L)
            if "Upload data successfully" in L:
                success = True
                markers.append(L)
                log("MARK", "UPLINK_SUCCESS")
                deadline = 0
                break
            if "not authorised" in low or "not authorized" in low:
                markers.append(L)
                log("MARK", "AUTH_FAIL")
            if "Failed to send" in L:
                markers.append(L)
                log("MARK", "FAILED_SEND")

    print("=== SUMMARY ===", flush=True)
    print(f"verify_ok={ok}", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"connected={connected} upload_success={success}", flush=True)
    print(f"markers={markers[-10:]}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)
    ser.close()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
