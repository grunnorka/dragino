#!/usr/bin/env python3
"""Test Failed-to-send / MQTT on EMQX Cloud (TLS 8883)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent

EMQX_HOST = "q933922f.ala.eu-central-1.emqxsl.com"
EMQX_PORT = 8883
EMQX_USER = "dragino"
EMQX_PASS = "dragino"
PUB = "dragino/ps-cb/up"
SUB = "dragino/ps-cb/down"

TB = "167.235.104.181,1883"
TB_TOKEN = "7donD0lgPwI5aJcS83dS"
TARGET_CYCLES = 2
LISTEN_S = 480


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
    logpath = ROOT / "logs" / f"{stamp}_failed_send_emqx_cloud.raw.log"
    ser = serial.Serial("COM8", 9600, timeout=0.2)
    print(f"Opened COM8; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(EMQX_PASS, "***")
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

    def unlock(n: int = 12) -> bool:
        for _ in range(n):
            if any("Password Correct" in L for L in send(pin, 2.0)):
                return True
            send(f"AT+PIN={pin}", 1.2)
            time.sleep(0.3)
        return False

    def query() -> dict[str, str]:
        info: dict[str, str] = {}
        for key, cmd in [
            ("servaddr", "AT+SERVADDR=?"),
            ("bkdns", "AT+BKDNS=?"),
            ("pro", "AT+PRO=?"),
            ("tdc", "AT+TDC=?"),
            ("pub", "AT+PUBTOPIC=?"),
            ("sub", "AT+SUBTOPIC=?"),
            ("uname", "AT+UNAME=?"),
            ("tls", "AT+TLSMOD=?"),
        ]:
            for L in send(cmd, 1.8):
                t = L.strip()
                if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                    continue
                info[key] = t
                break
        return info

    def apply_emqx() -> None:
        """MQTT JSON + EMQX Cloud TLS. No ATZ (avoid public HiveMQ rewrite)."""
        send("AT+PRO=3,5", 2.5)
        send("AT+TLSMOD=1,0", 2.0)
        send(f"AT+SERVADDR={EMQX_HOST},{EMQX_PORT}", 2.5)
        send("AT+BKDNS=1,0", 2.0)
        send(f"AT+UNAME={EMQX_USER}", 1.5)
        send(f"AT+PWD={EMQX_PASS}", 1.5)
        send(f"AT+PUBTOPIC={PUB}", 1.5)
        send(f"AT+SUBTOPIC={SUB}", 1.5)
        send("AT+CLIENT=null", 1.5)
        send("AT+MQOS=1", 1.5)
        send("AT+TDC=180", 1.5)
        send("AT+CDP=0", 2.0)

    def restore_tb() -> None:
        unlock()
        send("AT+PRO=3,3", 2.5)
        send("AT+TLSMOD=0,0", 2.0)
        send(f"AT+SERVADDR={TB}", 2.0)
        send(f"AT+BKDNS=1,0,{TB}", 2.5)
        send(f"AT+UNAME={TB_TOKEN}", 1.5)
        send("AT+PWD=NULL", 1.5)
        send("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5)
        send("AT+SUBTOPIC=v1/devices/me/attributes", 1.5)
        send("AT+TDC=180", 1.5)

    read_lines(1.0)
    ok = unlock()
    log("TEST", f"unlock={ok}")
    apply_emqx()
    unlock()
    send(f"AT+SERVADDR={EMQX_HOST},{EMQX_PORT}", 2.5)
    send("AT+BKDNS=1,0", 2.0)
    send("AT+TLSMOD=1,0", 2.0)
    cfg = query()
    log("TEST", f"CFG {cfg}")

    cycles: list[list[str]] = []
    current: list[str] = []
    phase = "idle"
    deadline = time.time() + LISTEN_S
    log("TEST", f"LISTEN {TARGET_CYCLES} EMQX Cloud cycles, up to {LISTEN_S}s")

    while time.time() < deadline and len(cycles) < TARGET_CYCLES:
        for L in read_lines(1.0):
            if "Upload start" in L:
                phase = "upload"
                current = [L]
                log("MARK", f"CYCLE_{len(cycles)+1}_BEGIN")
            elif phase == "upload":
                current.append(L)
                if "End of upload" in L:
                    for T in read_lines(20.0):
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

    summaries = []
    for i, cyc in enumerate(cycles, 1):
        s = {
            "n": i,
            "failed": sum(1 for L in cyc if "Failed to send" in L),
            "success": sum(1 for L in cyc if "Upload data successfully" in L),
            "subscribe_ok": sum(1 for L in cyc if "Subscribe to topic successfully" in L),
            "tcp_fail_close": sum(1 for L in cyc if "Failed to close TCP" in L),
            "mqtt_open": sum(1 for L in cyc if "Opened the MQTT" in L),
            "connected": sum(1 for L in cyc if "Successfully connected to the server" in L),
            "domain": [L for L in cyc if "Domain IP" in L][:3],
            "tls_or_ssl": [L for L in cyc if "TLS" in L or "SSL" in L or "certificate" in L.lower()][:5],
            "fail_other": [
                L
                for L in cyc
                if "Fail" in L or "fail" in L or "ERROR" in L
            ][:8],
        }
        summaries.append(s)
        log("RESULT", f"cycle{i}={s}")
        for L in cyc:
            if any(
                k in L
                for k in (
                    "Upload",
                    "Failed",
                    "TCP",
                    "MQTT",
                    "Domain",
                    "Subscribe",
                    "connected",
                    "Resolving",
                    "End of",
                    "TLS",
                    "SSL",
                    "emqx",
                    "8883",
                )
            ):
                log(f"C{i}", L)

    log("TEST", "RESTORE_PRIVATE_TB")
    restore_tb()
    final = query()
    log("TEST", f"FINAL_CFG {final}")

    print("=== SUMMARY ===", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"cycles_captured={len(cycles)}", flush=True)
    for s in summaries:
        print(s, flush=True)
    print(f"RESTORED={final}", flush=True)
    ser.close()
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
