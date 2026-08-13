#!/usr/bin/env python3
"""Point LTC2 at new Railway forwarder :24233; verify; listen for uplink. No ATZ."""
from __future__ import annotations

import os
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
    logpath = ROOT / "logs" / f"{stamp}_ltc2_proxy24233.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; target={IP},{PORT}; log={logpath}", flush=True)
    print("\n>>> HOLD ACT 1-3s on LTC2 NOW <<<\n", flush=True)
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

    def unlock(timeout: float = 180.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any("Password Correct" in L for L in send(PIN, 0.9)):
                log("TEST", "UNLOCK_OK")
                return True
            if any("LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
                log("TEST", "UNLOCK_OK already")
                return True
        return False

    def qval(cmd: str) -> str:
        send(PIN, 0.4)
        for L in send(cmd, 1.5):
            t = L.strip()
            if (
                not t
                or t == "OK"
                or t.startswith("AT+")
                or t.startswith("[")
                or "Password" in t
                or "Attention" in t
            ):
                continue
            return t
        return ""

    if not unlock():
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    # Apply — verify each critical field sticks (no ATZ)
    for cmd, key, expect in [
        ("AT+PRO=3,5", "PRO", "3,5"),
        ("AT+TLSMOD=0,0", "TLSMOD", "0,0"),
        (f"AT+SERVADDR={IP},{PORT}", "SERVADDR", f"{IP},{PORT}"),
        (f"AT+BKDNS=1,0,{IP},{PORT}", "BKDNS", IP),
        (f"AT+CLIENT={CLIENT}", "CLIENT", CLIENT),
        (f"AT+UNAME={USER}", "UNAME", USER),
        (f"AT+PWD={mqtt_pass}", "PWD", None),
        (f"AT+PUBTOPIC={PUB}", "PUBTOPIC", PUB),
        (f"AT+SUBTOPIC={SUB}", "SUBTOPIC", SUB),
        ("AT+MQOS=1", "MQOS", "1"),
        (f"AT+SERVADDR={IP},{PORT}", "SERVADDR", f"{IP},{PORT}"),
        (f"AT+BKDNS=1,0,{IP},{PORT}", "BKDNS", IP),
    ]:
        for attempt in range(4):
            send(PIN, 0.4)
            send(cmd, 1.7)
            if expect is None:
                break
            got = qval(f"AT+{key}=?")
            log("TEST", f"{key}={got!r}")
            if expect in got:
                break
            log("TEST", f"retry {key} #{attempt+1}")
        else:
            if expect is not None:
                log("TEST", f"FAIL_STICK {key}")

    for key in ("SERVADDR", "BKDNS", "UNAME", "CLIENT", "PUBTOPIC", "PRO", "TLSMOD"):
        log("FINAL", f"{key}={qval(f'AT+{key}=?')}")

    print("\n>>> HOLD ACT 1-3s to TRIGGER UPLINK <<<\n", flush=True)
    log("TEST", "LISTEN_UPLINK 150s")
    flags = dict(opened=False, connected=False, upload=False, failed=False, open_fail=False)
    end = time.time() + 150
    while time.time() < end:
        for L in read_lines(1.0):
            if "Opened the MQTT" in L:
                flags["opened"] = True
                log("MARK", "OPENED")
            if "Failed to open the MQTT" in L:
                flags["open_fail"] = True
                log("MARK", "OPEN_FAIL")
            if "Successfully connected to the server" in L:
                flags["connected"] = True
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] = True
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                flags["failed"] = True
                log("MARK", "FAILED_SEND")
        if flags["connected"] or flags["upload"]:
            read_lines(8.0)
            break

    print("=== SUMMARY ===", flush=True)
    print(flags, flush=True)
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)
    ser.close()
    raise SystemExit(0 if flags["connected"] or flags["upload"] else 1)


if __name__ == "__main__":
    main()
