#!/usr/bin/env python3
"""Listen for Failed to send through a full End-of-upload cycle."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
TB = "167.235.104.181,1883"


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
    logpath = ROOT / "logs" / f"{stamp}_failed_to_send_cycle2.raw.log"
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

    def unlock(n: int = 10) -> bool:
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
    unlock()
    # Restore private IP only (no PRO / no ATZ) so we can compare pattern on TB
    send(f"AT+SERVADDR={TB}", 2.0)
    send(f"AT+BKDNS=1,0,{TB}", 2.5)
    cfg = query()
    log("TEST", f"CFG {cfg}")

    log("TEST", "WAIT up to 240s for Upload start -> End of upload")
    deadline = time.time() + 240
    cycle: list[str] = []
    phase = "idle"
    while time.time() < deadline:
        for L in read_lines(1.0):
            if "Upload start" in L:
                phase = "upload"
                cycle = [L]
                log("MARK", "BEGIN")
            elif phase == "upload":
                cycle.append(L)
                if "End of upload" in L:
                    log("MARK", "END")
                    # catch any trailing MQTT retry after End of upload
                    for T in read_lines(40.0):
                        cycle.append(T)
                    phase = "done"
                    deadline = 0
                    break

    failed = [L for L in cycle if "Failed to send" in L]
    ok_up = [L for L in cycle if "Upload data successfully" in L]
    tcp_fail = [L for L in cycle if "Failed to close TCP" in L or "TCP connection is closed" in L]
    mqtt_ok = [L for L in cycle if "Opened the MQTT" in L or "connected to the server" in L]
    domain = [L for L in cycle if "Domain IP" in L]

    log(
        "RESULT",
        f"phase={phase} failed={len(failed)} success={len(ok_up)} "
        f"tcp_lines={len(tcp_fail)} mqtt_lines={len(mqtt_ok)} cfg={cfg}",
    )
    for L in cycle:
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
            log("CYCLE", L)

    print("=== SUMMARY ===", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"Failed to send: {len(failed)}", flush=True)
    print(f"Upload data successfully: {len(ok_up)}", flush=True)
    print(f"Domain lines: {domain}", flush=True)
    ser.close()
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
