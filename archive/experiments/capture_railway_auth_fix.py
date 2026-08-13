#!/usr/bin/env python3
"""Fix Railway MQTT auth: unique CLIENT + reassert UNAME/PWD, capture cycles."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent

HOST_IP = "66.33.22.220"  # sticky DNS safer
PORT = 33239
USER = "dragino"
PASS = "DrgN0-MqTt-7kR9wX2pL"
CLIENT = "dragino-pscb-869181074157262"  # unique, not null
PUB = "dragino/ps-cb/up"
SUB = "dragino/ps-cb/down"

TB = "167.235.104.181,1883"
TB_TOKEN = "7donD0lgPwI5aJcS83dS"
LISTEN_S = 420
TARGET_CYCLES = 2


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv(ROOT / ".env")
    pin = os.environ["DRAGINO_PIN"].strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_railway_auth_fix.raw.log"
    ser = serial.Serial("COM8", 9600, timeout=0.2)
    print(f"Opened COM8; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(PASS, "***")
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

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def unlock(n: int = 14) -> bool:
        for _ in range(n):
            if any("Password Correct" in L for L in send(pin, 2.0)):
                return True
            send(f"AT+PIN={pin}", 1.2)
            time.sleep(0.25)
        return False

    def wait_idle(max_s: float = 100.0) -> None:
        log("TEST", f"WAIT_IDLE {max_s}s")
        deadline = time.time() + max_s
        quiet_since = None
        while time.time() < deadline:
            lines = read_lines(1.0)
            for L in lines:
                if "Upload start" in L:
                    quiet_since = None
                if "power-off successful" in L or "End of upload" in L:
                    quiet_since = time.time()
            if quiet_since and time.time() - quiet_since >= 6:
                log("TEST", "IDLE_OK")
                return
            if not lines and quiet_since is None:
                if not read_lines(4.0):
                    log("TEST", "IDLE_QUIET")
                    return
        log("TEST", "IDLE_TIMEOUT")

    def query() -> dict[str, str]:
        info: dict[str, str] = {}
        for key, cmd in [
            ("servaddr", "AT+SERVADDR=?"),
            ("bkdns", "AT+BKDNS=?"),
            ("pro", "AT+PRO=?"),
            ("client", "AT+CLIENT=?"),
            ("uname", "AT+UNAME=?"),
            ("pwd", "AT+PWD=?"),
            ("pub", "AT+PUBTOPIC=?"),
            ("sub", "AT+SUBTOPIC=?"),
            ("tls", "AT+TLSMOD=?"),
            ("tdc", "AT+TDC=?"),
        ]:
            for L in send(cmd, 1.8):
                t = L.strip()
                if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                    continue
                info[key] = t
                break
        return info

    def apply() -> None:
        """Apply Railway MQTT with unique client + credentials. No ATZ."""
        unlock()
        cmds = [
            ("AT+PRO=3,5", 2.5),
            ("AT+TLSMOD=0,0", 2.0),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 2.5),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 2.5),
            (f"AT+CLIENT={CLIENT}", 2.0),
            (f"AT+UNAME={USER}", 1.8),
            (f"AT+PWD={PASS}", 1.8),
            (f"AT+PUBTOPIC={PUB}", 1.8),
            (f"AT+SUBTOPIC={SUB}", 1.8),
            ("AT+MQOS=1", 1.5),
            ("AT+TDC=180", 1.5),
            ("AT+CDP=0", 2.0),
        ]
        for cmd, w in cmds:
            send(cmd, w)
        # Re-assert auth fields while unlocked (password window often expires mid-apply)
        unlock()
        for cmd, w in [
            (f"AT+CLIENT={CLIENT}", 2.0),
            (f"AT+UNAME={USER}", 1.8),
            (f"AT+PWD={PASS}", 1.8),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 2.0),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 2.0),
            (f"AT+PUBTOPIC={PUB}", 1.5),
            (f"AT+SUBTOPIC={SUB}", 1.5),
            ("AT+TLSMOD=0,0", 1.5),
        ]:
            send(cmd, w)

    def restore_tb() -> None:
        unlock()
        for cmd, w in [
            ("AT+PRO=3,3", 2.5),
            ("AT+TLSMOD=0,0", 2.0),
            (f"AT+SERVADDR={TB}", 2.0),
            (f"AT+BKDNS=1,0,{TB}", 2.5),
            ("AT+CLIENT=null", 1.5),
            (f"AT+UNAME={TB_TOKEN}", 1.5),
            ("AT+PWD=NULL", 1.5),
            ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5),
            ("AT+SUBTOPIC=v1/devices/me/attributes", 1.5),
            ("AT+TDC=180", 1.5),
        ]:
            send(cmd, w)
        unlock()
        send(f"AT+SERVADDR={TB}", 2.0)
        send(f"AT+BKDNS=1,0,{TB}", 2.0)

    read_lines(1.0)
    wait_idle(90.0)
    log("TEST", f"unlock={unlock()}")
    apply()
    cfg = query()
    log("TEST", f"CFG {cfg}")
    # Sanity: refuse to listen if client still null or uname wrong
    client = (cfg.get("client") or "").lower()
    uname = cfg.get("uname") or ""
    if "null" in client or not client:
        log("TEST", "WARN client still null/empty — retry CLIENT once")
        unlock()
        send(f"AT+CLIENT={CLIENT}", 2.0)
        cfg = query()
        log("TEST", f"CFG2 {cfg}")

    cycles: list[list[str]] = []
    current: list[str] = []
    phase = "idle"
    deadline = time.time() + LISTEN_S
    log("TEST", f"LISTEN {TARGET_CYCLES} cycles / {LISTEN_S}s — watch Railway for client={CLIENT}")

    while time.time() < deadline and len(cycles) < TARGET_CYCLES:
        for L in read_lines(1.0):
            if "Upload start" in L:
                phase = "upload"
                current = [L]
                log("MARK", f"CYCLE_{len(cycles)+1}_BEGIN")
            elif phase == "upload":
                current.append(L)
                if "End of upload" in L:
                    for T in read_lines(25.0):
                        current.append(T)
                        if "Upload start" in T:
                            cycles.append(current[:-1])
                            log("MARK", f"CYCLE_{len(cycles)}_END")
                            current = [T]
                            log("MARK", f"CYCLE_{len(cycles)+1}_BEGIN")
                            break
                    else:
                        cycles.append(current)
                        log("MARK", f"CYCLE_{len(cycles)}_END")
                        phase = "idle"
                        current = []
                        if len(cycles) >= TARGET_CYCLES:
                            deadline = 0
                            break

    for i, cyc in enumerate(cycles, 1):
        s = {
            "n": i,
            "failed": sum(1 for L in cyc if "Failed to send" in L),
            "success": sum(1 for L in cyc if "Upload data successfully" in L),
            "subscribe_ok": sum(1 for L in cyc if "Subscribe to topic successfully" in L),
            "mqtt_open": sum(1 for L in cyc if "Opened the MQTT" in L),
            "connected": sum(1 for L in cyc if "Successfully connected to the server" in L),
            "domain": [L for L in cyc if "Domain IP" in L][:4],
            "fail_other": [L for L in cyc if "Fail" in L or "ERROR" in L][:8],
        }
        log("RESULT", f"cycle{i}={s}")
        for L in cyc:
            if any(
                k in L
                for k in (
                    "Upload",
                    "Failed",
                    "MQTT",
                    "Domain",
                    "Subscribe",
                    "connected",
                    "End of",
                    "33239",
                    "66.33",
                )
            ):
                log(f"C{i}", L)

    # Leave on Railway for monitor — do NOT restore TB unless asked
    log("TEST", "LEAVE_ON_RAILWAY (no TB restore)")
    final = query()
    log("TEST", f"FINAL_CFG {final}")
    print("=== SUMMARY ===", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"FINAL={final}", flush=True)
    print(f"cycles={len(cycles)}", flush=True)
    ser.close()
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
