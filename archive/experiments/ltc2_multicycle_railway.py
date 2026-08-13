#!/usr/bin/env python3
"""AFK multi-cycle LTC2 -> Railway hayabusa:24233 until uplink succeeds.

- Shorten TDC to 60 (fallback 120)
- Quiet-window apply SERVADDR/auth/topics
- Wait full cycles; re-pin on Failed to send
- Capture Domain IP / CSQ / diag for root-cause
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
IP, PORT = "66.33.22.223", "24233"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
MAX_CYCLES = 8
LISTEN_BUDGET_S = 720  # ~12 min covers several 60s cycles + modem wake


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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_multicycle.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; target={IP},{PORT}; log={logpath}", flush=True)
    print("AFK mode: waiting for UART wake (no ACT required if cycle wakes UART)", flush=True)
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

    def send(cmd: str, wait: float = 1.5) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def unlock(timeout: float = 300.0) -> bool:
        log("TEST", f"UNLOCK_WAIT {timeout}s")
        deadline = time.time() + timeout
        while time.time() < deadline:
            # also catch spontaneous UART wake during uplink
            lines = read_lines(0.4)
            if any("Password Correct" in L or "LTC2-CB" in L or "Upload start" in L for L in lines):
                if any("Password Correct" in L for L in send(PIN, 0.9)):
                    log("TEST", "UNLOCK_OK")
                    return True
                if any("LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
                    log("TEST", "UNLOCK_OK already")
                    return True
            if any("Password Correct" in L for L in send(PIN, 0.85)):
                log("TEST", "UNLOCK_OK")
                return True
            if any("LTC2-CB" in L for L in send("AT+MODEL=?", 0.95)):
                log("TEST", "UNLOCK_OK already")
                return True
        return False

    def quiet_window(max_wait: float = 75.0) -> None:
        log("TEST", "QUIET_WAIT")
        end = time.time() + max_wait
        streak = 0
        while time.time() < end and streak < 5:
            lines = read_lines(1.0)
            noisy = any(
                any(
                    x in L
                    for x in (
                        "MQTT",
                        "upload",
                        "Upload",
                        "Failed",
                        "TCP",
                        "NB module",
                        "Connecting",
                        "Domain",
                    )
                )
                for L in lines
            )
            streak = 0 if noisy else streak + 1
        log("TEST", f"QUIET_OK streak={streak}")

    def qval(cmd: str) -> str:
        send(PIN, 0.3)
        for L in send(cmd, 1.6):
            t = L.strip()
            if (
                not t
                or t == "OK"
                or t.startswith("AT+")
                or t.startswith("[")
                or "Password" in t
                or "Attention" in t
                or "Failed" in t
                or "MQTT" in t
                or "TCP" in t
                or "Upload" in t
                or "Domain" in t
                or "Closing" in t
                or "module" in t
                or "Signal" in t
                or "NBIOT" in t
                or "Echo" in t
                or "IMEI" in t
                or "IMSI" in t
                or "APN" in t
                or "Configure" in t
                or "data format" in t
                or "Model information" in t
                or t.startswith("AT+PWR")
            ):
                continue
            return t
        return ""

    def apply_core() -> None:
        cmds = [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={IP},{PORT}", 1.6),
            (f"AT+BKDNS=2,4,{IP},{PORT}", 1.8),
            (f"AT+BKDNS=1,0,{IP},{PORT}", 1.6),
            (f"AT+CLIENT={CLIENT}", 1.2),
            (f"AT+UNAME={USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
            ("AT+MQOS=1", 1.1),
            (f"AT+SERVADDR={IP},{PORT}", 1.4),
            (f"AT+BKDNS=1,0,{IP},{PORT}", 1.4),
            (f"AT+CLIENT={CLIENT}", 1.1),
            (f"AT+UNAME={USER}", 1.1),
            (f"AT+PWD={mqtt_pass}", 1.1),
            (f"AT+PUBTOPIC={PUB}", 1.1),
            (f"AT+SUBTOPIC={SUB}", 1.1),
        ]
        for cmd, w in cmds:
            send(PIN, 0.3)
            send(cmd, w)

    def set_tdc() -> int:
        for val in (60, 120):
            send(PIN, 0.3)
            send(f"AT+TDC={val}", 1.5)
            got = qval("AT+TDC=?")
            log("TEST", f"TDC_READBACK={got!r} want={val}")
            if str(val) in got:
                return val
        return -1

    def verify() -> dict[str, str]:
        keys = [
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("PRO", "AT+PRO=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("UNAME", "AT+UNAME=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("SUBTOPIC", "AT+SUBTOPIC=?"),
            ("TLSMOD", "AT+TLSMOD=?"),
            ("TDC", "AT+TDC=?"),
        ]
        out: dict[str, str] = {}
        for k, cmd in keys:
            out[k] = qval(cmd)
            log("CFG", f"{k}={out[k]}")
        return out

    def cfg_ok(cfg: dict[str, str]) -> bool:
        blob = str(cfg).lower()
        return (
            IP in cfg.get("SERVADDR", "")
            and PORT in cfg.get("SERVADDR", "")
            and "3,5" in cfg.get("PRO", "")
            and cfg.get("CLIENT") == CLIENT
            and cfg.get("UNAME") == USER
            and PUB in cfg.get("PUBTOPIC", "")
            and SUB in cfg.get("SUBTOPIC", "")
            and "0,0" in cfg.get("TLSMOD", "")
            and "hivemq" not in blob
        )

    def diag() -> None:
        log("TEST", "DIAG")
        send(PIN, 0.4)
        for cmd in (
            "AT+SERVADDR=?",
            "AT+BKDNS=?",
            "AT+CSQ=?",
            "AT+CIMI=?",
            "AT+CGPADDR=?",
            "AT+CCID=?",
            "AT+NETTYPE=?",
            "AT+CLIENT=?",
            "AT+UNAME=?",
            "AT+PUBTOPIC=?",
            "AT+PRO=?",
            "AT+TLSMOD=?",
            "AT+TDC=?",
        ):
            try:
                send(cmd, 1.5)
            except Exception as exc:
                log("TEST", f"diag skip {cmd}: {exc}")

    if not unlock(300):
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    quiet_window(60)
    send(PIN, 0.5)
    tdc = set_tdc()
    apply_core()
    cfg = verify()
    ok = cfg_ok(cfg)
    log("TEST", f"VERIFY={'PASS' if ok else 'FAIL'} TDC={tdc} BKDNS={cfg.get('BKDNS')}")

    if not (IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")):
        send(PIN, 0.3)
        send(f"AT+SERVADDR={IP},{PORT}", 1.6)
        send(PIN, 0.3)
        send(f"AT+BKDNS=1,0,{IP},{PORT}", 1.6)
        cfg = verify()
        ok = cfg_ok(cfg)
        log("TEST", f"VERIFY_RETRY={'PASS' if ok else 'FAIL'}")

    if "hivemq" in str(cfg).lower():
        log("TEST", "HIVEMQ_DETECTED — re-pin SERVADDR")
        send(PIN, 0.3)
        send(f"AT+SERVADDR={IP},{PORT}", 1.6)
        send(PIN, 0.3)
        send(f"AT+BKDNS=1,0,{IP},{PORT}", 1.6)
        cfg = verify()

    # Listen across multiple TDC cycles; re-pin after each Failed to send
    log("TEST", f"LISTEN_MULTICYCLE budget={LISTEN_BUDGET_S}s max_cycles={MAX_CYCLES}")
    flags = {
        "opened": False,
        "connected": False,
        "upload": False,
        "failed": 0,
        "open_fail": 0,
        "cycles": 0,
        "domain": "",
        "last_fail_utc": "",
    }
    markers: list[str] = []
    end = time.time() + LISTEN_BUDGET_S
    last_fail_handled = 0.0

    while time.time() < end and not flags["upload"]:
        for L in read_lines(1.0):
            markers.append(L)
            if "Upload start" in L:
                flags["cycles"] += 1
                log("MARK", f"CYCLE_START n={flags['cycles']}")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L[:120]}")
            if "Opened the MQTT" in L:
                flags["opened"] = True
                log("MARK", "OPENED")
            if "Failed to open the MQTT" in L:
                flags["open_fail"] += 1
                log("MARK", "OPEN_FAIL")
            if "Successfully connected to the server" in L:
                flags["connected"] = True
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] = True
                log("MARK", "UPLOAD_OK")
                end = 0
                break
            if "not authorised" in L.lower() or "not authorized" in L.lower():
                log("MARK", "AUTH_FAIL")
            if "Failed to send" in L:
                flags["failed"] += 1
                flags["last_fail_utc"] = datetime.now(timezone.utc).isoformat()
                log("MARK", f"FAILED_SEND n={flags['failed']}")
                # After end-of-upload / quiet, re-pin once per fail burst
                if time.time() - last_fail_handled > 25:
                    last_fail_handled = time.time()
                    # let teardown finish
                    read_lines(8.0)
                    send(PIN, 0.5)
                    diag()
                    quiet_window(40)
                    send(PIN, 0.4)
                    apply_core()
                    cfg = verify()
                    log("TEST", f"REPIN_AFTER_FAIL cfg_ok={cfg_ok(cfg)} SERVADDR={cfg.get('SERVADDR')}")
                    if tdc < 0 or str(tdc) not in cfg.get("TDC", ""):
                        tdc = set_tdc()
        if flags["cycles"] >= MAX_CYCLES and flags["failed"] >= MAX_CYCLES and not flags["upload"]:
            log("TEST", "MAX_CYCLES_REACHED")
            break

    # final cfg snapshot if unlocked
    send(PIN, 0.4)
    if any("Password Correct" in L or "LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
        cfg = verify()

    print("=== SUMMARY ===", flush=True)
    print(f"tdc={tdc}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"markers_tail={markers[-20:]}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"UTC_END={datetime.now(timezone.utc).isoformat()}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["upload"] or flags["connected"] else 1)


if __name__ == "__main__":
    main()
