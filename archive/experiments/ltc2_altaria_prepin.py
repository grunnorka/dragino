#!/usr/bin/env python3
"""Pin LTC2 to altaria:33239 in the PRE-upload RDY/CSQ window, then listen-only.

TDC should already be 60. On RDY/Signal Strength: unlock + SERVADDR/BKDNS burst
BEFORE Upload start so the dial uses the new address. No AT during MQTT connect.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
IP, PORT = "66.33.22.220", "33239"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
PASS = "DrgN0-MqTt-7kR9wX2pL"
envp = ROOT / "railway-mqtt.local.env"
if envp.is_file():
    for raw in envp.read_text(encoding="utf-8").splitlines():
        if raw.startswith("MQTT_PASS="):
            PASS = raw.split("=", 1)[1].strip()
        if raw.startswith("MQTT_FALLBACK_IP="):
            IP = raw.split("=", 1)[1].strip()
        if raw.startswith("MQTT_PORT=") and "LTC2" not in raw:
            # only top-level MQTT_PORT from file — force 33239
            pass
PORT = "33239"
IP = "66.33.22.220"


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_altaria_prepin.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.1)
    print(f"Opened {COM}; prepin {IP},{PORT}; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(PASS, "***").replace(PIN, "***PIN***")
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

    def send(cmd: str, wait: float = 0.7) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def prepin() -> dict[str, str]:
        """Ultra-fast address pin; verify SERVADDR immediately."""
        log("TEST", "PREPIN_BURST")
        send(PIN, 0.35)
        for _ in range(4):
            send(f"AT+SERVADDR={IP},{PORT}", 0.55)
            send(PIN, 0.15)
            send(f"AT+BKDNS=1,0,{IP},{PORT}", 0.55)
            send(PIN, 0.15)
        for cmd in (
            "AT+PRO=3,5",
            "AT+TLSMOD=0,0",
            f"AT+CLIENT={CLIENT}",
            f"AT+UNAME={USER}",
            f"AT+PWD={PASS}",
            f"AT+PUBTOPIC={PUB}",
            f"AT+SUBTOPIC={SUB}",
            "AT+TDC=60",
            f"AT+SERVADDR={IP},{PORT}",
            f"AT+BKDNS=1,0,{IP},{PORT}",
        ):
            send(PIN, 0.12)
            send(cmd, 0.5)
        # immediate SERVADDR read (most important)
        send(PIN, 0.15)
        serv = ""
        for L in send("AT+SERVADDR=?", 0.9):
            t = L.strip()
            if IP in t and PORT in t:
                serv = t
                break
            if t and t != "OK" and not t.startswith("[") and "," in t and "Password" not in t:
                serv = t
        send(PIN, 0.15)
        bk = ""
        for L in send("AT+BKDNS=?", 0.9):
            t = L.strip()
            if t.startswith("1,") or IP in t:
                bk = t
                break
        cfg = {"SERVADDR": serv, "BKDNS": bk}
        log("CFG", f"SERVADDR={serv}")
        log("CFG", f"BKDNS={bk}")
        ok = IP in serv and PORT in serv
        log("TEST", f"PREPIN ok={ok}")
        if not ok:
            # one more hard retry
            send(PIN, 0.2)
            send(f"AT+SERVADDR={IP},{PORT}", 0.6)
            send(PIN, 0.15)
            for L in send("AT+SERVADDR=?", 0.9):
                if IP in L and PORT in L:
                    cfg["SERVADDR"] = L.strip()
                    ok = True
                    log("CFG", f"SERVADDR_RETRY={cfg['SERVADDR']}")
                    log("TEST", "PREPIN ok=True (retry)")
        return cfg

    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "cycles": 0,
        "domain": "",
        "prepinned": False,
        "serv_ok": False,
    }
    cfg: dict[str, str] = {}
    in_upload = False
    end = time.time() + 720

    log("TEST", "WAIT RDY/Signal then PREPIN; listen-only during MQTT")
    while time.time() < end and flags["upload"] == 0:
        for L in read_lines(1.0):
            # Pre-upload warm window
            if (not in_upload) and (
                L.strip() == "RDY"
                or "Signal Strength" in L
                or "Echo mode turned off" in L
                or "Password timeout" in L
            ):
                if not flags["prepinned"] or not flags["serv_ok"]:
                    send(PIN, 0.3)
                    cfg = prepin()
                    flags["prepinned"] = True
                    flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get(
                        "SERVADDR", ""
                    )

            if "Upload start" in L:
                flags["cycles"] += 1
                in_upload = True
                log("MARK", f"CYCLE n={flags['cycles']} serv_ok={flags['serv_ok']} SERVADDR={cfg.get('SERVADDR')}")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L[:160]}")
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Successfully connected to the server" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", f"FAILED_SEND n={flags['failed']}")
            if "End of upload" in L:
                in_upload = False
                # after fail, try prepin again for next cycle while warm
                read_lines(1.5)
                send(PIN, 0.3)
                cfg = prepin()
                flags["serv_ok"] = IP in cfg.get("SERVADDR", "") and PORT in cfg.get(
                    "SERVADDR", ""
                )
                log("TEST", f"POST_END_PREPIN serv_ok={flags['serv_ok']}")

        if flags["failed"] >= 4 and flags["upload"] == 0 and flags["cycles"] >= 3:
            log("TEST", "STOP after multi-cycle fail")
            # quiet diag
            send(PIN, 0.4)
            for cmd in ("AT+SERVADDR=?", "AT+BKDNS=?", "AT+CSQ=?", "AT+CGPADDR=?", "AT+CIMI=?"):
                send(cmd, 1.0)
            break

    print("=== SUMMARY ===", flush=True)
    print(f"target={IP},{PORT}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["upload"] or flags["connected"] else 1)


if __name__ == "__main__":
    main()
