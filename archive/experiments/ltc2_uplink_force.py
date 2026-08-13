#!/usr/bin/env python3
"""Quiet-window apply LTC2 -> hayabusa:24233, ATZ, listen uplink + diag on fail."""
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
    logpath = ROOT / "logs" / f"{stamp}_ltc2_uplink_force.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; target={IP},{PORT}; log={logpath}", flush=True)
    print("\n>>> HOLD ACT 1-3s on LTC2 NOW (wake UART) <<<\n", flush=True)
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
            lines = send(PIN, 0.9)
            if any("Password Correct" in L for L in lines):
                log("TEST", "UNLOCK_OK")
                return True
            if any("LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
                log("TEST", "UNLOCK_OK already")
                return True
        return False

    def quiet_window(max_wait: float = 90.0) -> None:
        """Wait until modem not mid-upload so AT writes stick."""
        log("TEST", "QUIET_WAIT")
        end = time.time() + max_wait
        streak = 0
        while time.time() < end and streak < 4:
            lines = read_lines(1.2)
            noisy = any(
                any(x in L for x in ("MQTT", "upload", "Failed", "TCP", "NB module", "Password Incorrect"))
                for L in lines
            )
            streak = 0 if noisy else streak + 1
        log("TEST", f"QUIET_OK streak={streak}")

    def apply_core() -> None:
        for cmd, w in [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={IP},{PORT}", 1.6),
            # mode 2 stores fallback IP; mode 1,0 alone clears (docs)
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
        ]:
            send(PIN, 0.35)
            send(cmd, w)

    def qval(cmd: str) -> str:
        send(PIN, 0.35)
        for L in send(cmd, 1.5):
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
            ):
                continue
            return t
        return ""

    def verify() -> dict[str, str]:
        keys = {
            "SERVADDR": "AT+SERVADDR=?",
            "BKDNS": "AT+BKDNS=?",
            "PRO": "AT+PRO=?",
            "CLIENT": "AT+CLIENT=?",
            "UNAME": "AT+UNAME=?",
            "PUBTOPIC": "AT+PUBTOPIC=?",
            "SUBTOPIC": "AT+SUBTOPIC=?",
            "TLSMOD": "AT+TLSMOD=?",
        }
        out: dict[str, str] = {}
        for k, cmd in keys.items():
            out[k] = qval(cmd)
            log("CFG", f"{k}={out[k]}")
        return out

    if not unlock():
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    quiet_window(60)
    send(PIN, 0.5)
    apply_core()
    cfg = verify()
    ok = (
        IP in cfg.get("SERVADDR", "")
        and PORT in cfg.get("SERVADDR", "")
        and cfg.get("PRO") == "3,5"
        and cfg.get("CLIENT") == CLIENT
        and cfg.get("UNAME") == USER
        and PUB in cfg.get("PUBTOPIC", "")
        and SUB in cfg.get("SUBTOPIC", "")
        and "0,0" in cfg.get("TLSMOD", "")
        and "hivemq" not in str(cfg).lower()
    )
    log("TEST", f"VERIFY_PRE_ATZ={'PASS' if ok else 'FAIL'} BKDNS={cfg.get('BKDNS')}")
    if not ok:
        print("BLOCKER: CFG verify failed pre-ATZ", flush=True)
        ser.close()
        raise SystemExit(1)

    log("TEST", "ATZ")
    send("ATZ", 1.5)
    print("\n>>> REBOOT — HOLD ACT 1-3s in ~15s for re-unlock <<<\n", flush=True)
    time.sleep(14.0)
    if not unlock(180):
        log("TEST", "POST_ATZ_UNLOCK_FAIL")
        ser.close()
        raise SystemExit(3)

    quiet_window(45)
    send(PIN, 0.5)
    apply_core()
    cfg2 = verify()
    log("TEST", f"VERIFY_POST={cfg2}")

    # Force uplink: short TDC then wait (no second ATZ — avoid clearing BKDNS again)
    send(PIN, 0.4)
    send("AT+TDC=120", 1.5)
    print("\n>>> HOLD ACT 1-3s to TRIGGER UPLINK now <<<\n", flush=True)
    log("TEST", "LISTEN_UPLINK 180s")
    flags = {
        "opened": False,
        "connected": False,
        "upload": False,
        "failed": False,
        "open_fail": False,
        "domain": "",
    }
    markers: list[str] = []
    end = time.time() + 180
    while time.time() < end:
        for L in read_lines(1.0):
            markers.append(L)
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
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
                end = 0
                break
            if "Failed to send" in L:
                flags["failed"] = True
                log("MARK", "FAILED_SEND")
        if flags["upload"]:
            break

    if flags["failed"] and not flags["upload"]:
        log("TEST", "DIAG after Failed to send")
        send(PIN, 0.5)
        for cmd in (
            "AT+SERVADDR=?",
            "AT+BKDNS=?",
            "AT+CSQ=?",
            "AT+CIMI=?",
            "AT+CGPADDR=?",
            "AT+CCID=?",
            "AT+NETTYPE=?",
            "AT+LDATA=?",
        ):
            try:
                send(cmd, 1.6)
            except Exception as exc:
                log("TEST", f"diag skip {cmd}: {exc}")

    print("=== SUMMARY ===", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"cfg_post={cfg2}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"UPLINK_UTC_HINT={datetime.now(timezone.utc).isoformat()}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["connected"] or flags["upload"] else 1)


if __name__ == "__main__":
    main()
