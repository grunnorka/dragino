#!/usr/bin/env python3
"""Capture one PS-CB-NA uplink cycle and classify Failed to send."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
TB = "167.235.104.181,1883"
TOKEN = "7donD0lgPwI5aJcS83dS"


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
    pin = os.environ.get("DRAGINO_PIN", "").strip()
    if not pin:
        raise SystemExit("DRAGINO_PIN missing")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_failed_to_send_capture.raw.log"
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

    read_lines(1.5)
    ok = unlock()
    log("TEST", f"unlock={ok}")
    cfg = query()
    log("TEST", f"CFG_BEFORE {cfg}")

    # Keep private broker in RAM (no ATZ) so uplink hits TB, not HiveMQ
    if "hivemq" in str(cfg.get("servaddr", "")).lower() or "167.235.104.181" not in str(
        cfg.get("servaddr", "")
    ):
        log("TEST", "RESTORE_PRIVATE_NO_ATZ")
        unlock()
        for cmd, w in [
            (f"AT+SERVADDR={TB}", 2.0),
            (f"AT+BKDNS=1,0,{TB}", 2.5),
            ("AT+PRO=3,3", 2.0),
            (f"AT+UNAME={TOKEN}", 1.5),
            ("AT+PWD=NULL", 1.5),
            ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5),
            ("AT+SUBTOPIC=v1/devices/me/attributes", 1.5),
            ("AT+TDC=180", 1.5),
        ]:
            send(cmd, w)
        cfg = query()
        log("TEST", f"CFG_AFTER_RESTORE {cfg}")

    log("TEST", "LISTEN_UP_TO_300S_FOR_UPLOAD_CYCLE (antenna should be on)")
    deadline = time.time() + 300
    in_upload = False
    cycle: list[str] = []
    saw_upload_start = False
    while time.time() < deadline:
        for L in read_lines(1.0):
            if "Upload start" in L:
                in_upload = True
                saw_upload_start = True
                cycle = [L]
                log("MARK", "UPLOAD_CYCLE_BEGIN")
            elif in_upload:
                cycle.append(L)
                if "End of upload" in L:
                    log("MARK", "UPLOAD_CYCLE_END")
                    for T in read_lines(30.0):
                        if any(
                            k in T
                            for k in (
                                "MQTT",
                                "Upload",
                                "Failed",
                                "TCP",
                                "Domain",
                                "Subscribe",
                                "connected",
                                "Resolving",
                            )
                        ):
                            cycle.append(T)
                    deadline = 0
                    break

    failed = [L for L in cycle if "Failed to send" in L]
    ok_up = [L for L in cycle if "Upload data successfully" in L]
    hive = "hivemq" in str(cfg.get("servaddr", "")).lower() or any(
        "hivemq" in L.lower() for L in cycle
    )
    priv = "167.235.104.181" in str(cfg.get("servaddr", "")) or any(
        "167.235" in L for L in cycle
    )

    log(
        "RESULT",
        f"saw_upload_start={saw_upload_start} failed_count={len(failed)} "
        f"success_count={len(ok_up)} hive={hive} priv={priv} cfg={cfg}",
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
            )
        ):
            log("CYCLE", L)

    print("=== SUMMARY ===", flush=True)
    print(
        f"SERVADDR={cfg.get('servaddr')} PRO={cfg.get('pro')} TDC={cfg.get('tdc')}",
        flush=True,
    )
    print(f"saw_upload_start={saw_upload_start}", flush=True)
    print(f"Failed to send count in cycle: {len(failed)}", flush=True)
    print(f"Upload data successfully count: {len(ok_up)}", flush=True)
    print(f"hive={hive} priv={priv}", flush=True)
    ser.close()
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
