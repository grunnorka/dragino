#!/usr/bin/env python3
"""Fresh listen: 1-2 full uplink cycles for Failed to send analysis."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
TB = "167.235.104.181,1883"
TOKEN = "7donD0lgPwI5aJcS83dS"
TARGET_CYCLES = 2
LISTEN_S = 420  # up to ~7 min for 2x TDC=180


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
    logpath = ROOT / "logs" / f"{stamp}_failed_send_fresh.raw.log"
    ser = serial.Serial("COM8", 9600, timeout=0.2)
    print(f"Opened COM8; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {s}"
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
        ]:
            for L in send(cmd, 1.8):
                t = L.strip()
                if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                    continue
                info[key] = t
                break
        return info

    read_lines(1.0)
    ok = unlock()
    log("TEST", f"unlock={ok}")

    # Restore private broker in RAM only (no ATZ, no PRO rewrite)
    unlock()
    send(f"AT+SERVADDR={TB}", 2.0)
    send(f"AT+BKDNS=1,0,{TB}", 2.5)
    send(f"AT+UNAME={TOKEN}", 1.5)
    send("AT+PWD=NULL", 1.5)
    send("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5)
    send("AT+SUBTOPIC=v1/devices/me/attributes", 1.5)
    # Clear cached packets so cycles are clean
    send("AT+CDP=0", 2.0)
    cfg = query()
    log("TEST", f"CFG {cfg}")

    cycles: list[list[str]] = []
    current: list[str] = []
    phase = "idle"
    deadline = time.time() + LISTEN_S
    log("TEST", f"LISTEN {TARGET_CYCLES} cycles, up to {LISTEN_S}s")

    while time.time() < deadline and len(cycles) < TARGET_CYCLES:
        for L in read_lines(1.0):
            if "Upload start" in L:
                phase = "upload"
                current = [L]
                log("MARK", f"CYCLE_{len(cycles)+1}_BEGIN")
            elif phase == "upload":
                current.append(L)
                if "End of upload" in L:
                    # trailing window for MQTT retry after End of upload
                    for T in read_lines(20.0):
                        current.append(T)
                        if "Upload start" in T:
                            # next cycle started inside trailing window
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

    # Summarize each cycle
    summaries = []
    for i, cyc in enumerate(cycles, 1):
        failed = sum(1 for L in cyc if "Failed to send" in L)
        success = sum(1 for L in cyc if "Upload data successfully" in L)
        sub_ok = sum(1 for L in cyc if "Subscribe to topic successfully" in L)
        tcp = [L for L in cyc if "TCP" in L]
        domain = [L for L in cyc if "Domain IP" in L]
        hive = any("hivemq" in L.lower() or "18.158" in L or "18.156" in L or "52.59" in L for L in cyc)
        priv = any("167.235" in L for L in cyc + domain)
        s = {
            "n": i,
            "failed": failed,
            "success": success,
            "subscribe_ok": sub_ok,
            "tcp_lines": len(tcp),
            "domain": domain[:3],
            "hive_hints": hive,
            "priv_hints": priv,
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
                    "167.235",
                    "hivemq",
                )
            ):
                log(f"C{i}", L)

    print("=== SUMMARY ===", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"cycles_captured={len(cycles)}", flush=True)
    for s in summaries:
        print(s, flush=True)
    ser.close()
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
