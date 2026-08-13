#!/usr/bin/env python3
"""Test Dragino PS-CB-NA against Railway MQTT (plain TCP, no TLS)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent

HOST = "altaria.proxy.rlwy.net"
PORT = 33239
FALLBACK_IP = "66.33.22.220"
USER = "dragino"
PASS = "DrgN0-MqTt-7kR9wX2pL"
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
    logpath = ROOT / "logs" / f"{stamp}_railway_mqtt.raw.log"
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

    def unlock(n: int = 12) -> bool:
        for _ in range(n):
            if any("Password Correct" in L for L in send(pin, 2.0)):
                return True
            send(f"AT+PIN={pin}", 1.2)
            time.sleep(0.3)
        return False

    def wait_idle(max_s: float = 120.0) -> None:
        log("TEST", f"WAIT_IDLE up to {max_s}s")
        deadline = time.time() + max_s
        quiet_since = None
        while time.time() < deadline:
            lines = read_lines(1.0)
            for L in lines:
                if "Upload start" in L or "Failed to configure CA" in L:
                    quiet_since = None
                if "power-off successful" in L or "End of upload" in L:
                    quiet_since = time.time()
            if quiet_since and time.time() - quiet_since >= 6:
                log("TEST", "IDLE_OK")
                return
            if not lines and quiet_since is None:
                more = read_lines(4.0)
                if not more:
                    log("TEST", "IDLE_QUIET")
                    return
        log("TEST", "IDLE_TIMEOUT")

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

    def apply_railway(use_ip: bool = False) -> None:
        host = FALLBACK_IP if use_ip else HOST
        unlock()
        send("AT+PRO=3,5", 2.5)
        send("AT+TLSMOD=0,0", 2.0)
        send(f"AT+SERVADDR={host},{PORT}", 2.5)
        # Pin BKDNS to this host/IP so sticky private-TB DNS does not win
        send(f"AT+BKDNS=1,0,{host},{PORT}", 2.5)
        send(f"AT+UNAME={USER}", 1.5)
        send(f"AT+PWD={PASS}", 1.5)
        send(f"AT+PUBTOPIC={PUB}", 1.5)
        send(f"AT+SUBTOPIC={SUB}", 1.5)
        send("AT+CLIENT=null", 1.5)
        send("AT+MQOS=1", 1.5)
        send("AT+TDC=180", 1.5)
        send("AT+CDP=0", 2.0)
        unlock()
        send(f"AT+SERVADDR={host},{PORT}", 2.5)
        send(f"AT+BKDNS=1,0,{host},{PORT}", 2.5)
        send("AT+TLSMOD=0,0", 2.0)
        send(f"AT+PUBTOPIC={PUB}", 1.5)
        send(f"AT+SUBTOPIC={SUB}", 1.5)
        send(f"AT+UNAME={USER}", 1.5)
        send(f"AT+PWD={PASS}", 1.5)

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
        unlock()
        send(f"AT+SERVADDR={TB}", 2.0)
        send(f"AT+BKDNS=1,0,{TB}", 2.0)

    def listen_cycles(max_cycles: int, listen_s: float, label: str) -> tuple[list[list[str]], list[dict]]:
        cycles: list[list[str]] = []
        current: list[str] = []
        phase = "idle"
        deadline = time.time() + listen_s
        log("TEST", f"LISTEN {label}: up to {max_cycles} cycles / {listen_s}s")
        while time.time() < deadline and len(cycles) < max_cycles:
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
                            if len(cycles) >= max_cycles:
                                deadline = 0
                                break
        summaries = []
        for i, cyc in enumerate(cycles, 1):
            s = {
                "n": i,
                "failed": sum(1 for L in cyc if "Failed to send" in L),
                "success": sum(1 for L in cyc if "Upload data successfully" in L),
                "subscribe_ok": sum(1 for L in cyc if "Subscribe to topic successfully" in L),
                "mqtt_open": sum(1 for L in cyc if "Opened the MQTT" in L),
                "connected": sum(1 for L in cyc if "Successfully connected to the server" in L),
                "domain": [L for L in cyc if "Domain IP" in L][:4],
                "fail_other": [L for L in cyc if "Fail" in L or "fail" in L or "ERROR" in L][:8],
            }
            summaries.append(s)
            log("RESULT", f"{label}_cycle{i}={s}")
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
                        "rlwy",
                        "33239",
                        "66.33",
                    )
                ):
                    log(f"C{label}{i}", L)
        return cycles, summaries

    read_lines(1.0)
    wait_idle(90.0)
    ok = unlock()
    log("TEST", f"unlock={ok}")

    # Hostname first (1 cycle probe); on MQTT miss, fallback IP for up to 2 cycles
    apply_railway(use_ip=False)
    cfg = query()
    log("TEST", f"CFG_HOST {cfg}")
    cycles, summaries = listen_cycles(1, 240, "HOST")

    host_ok = any(s.get("connected", 0) > 0 or s.get("success", 0) > 0 for s in summaries)
    if host_ok:
        more, more_s = listen_cycles(1, 240, "HOST2")
        cycles.extend(more)
        summaries.extend(more_s)
    else:
        log("TEST", "FALLBACK_TO_IP 66.33.22.220")
        wait_idle(60.0)
        apply_railway(use_ip=True)
        cfg = query()
        log("TEST", f"CFG_IP {cfg}")
        cycles2, summaries2 = listen_cycles(TARGET_CYCLES, LISTEN_S, "IP")
        cycles.extend(cycles2)
        summaries.extend(summaries2)

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
