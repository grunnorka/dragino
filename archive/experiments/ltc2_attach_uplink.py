#!/usr/bin/env python3
"""Wait for NB attach, pin LTC2 to :24233, force uplink, capture diag."""
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
    logpath = ROOT / "logs" / f"{stamp}_ltc2_attach_uplink.raw.log"
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

    def wait_attach(timeout: float = 240.0) -> bool:
        """Wait until CSQ is not 99 and modem not initializing."""
        log("TEST", "WAIT_ATTACH")
        deadline = time.time() + timeout
        while time.time() < deadline:
            # keep session alive
            send(PIN, 0.4)
            lines = send("AT+CSQ=?", 1.8)
            csq = None
            for L in lines:
                m = re.search(r"Signal Strength:(\d+)", L)
                if m:
                    csq = int(m.group(1))
                m2 = re.match(r"^(\d+)(?:,\d+)?$", L.strip())
                if m2 and "Signal" not in L:
                    try:
                        csq = int(m2.group(1))
                    except ValueError:
                        pass
                if L.strip().isdigit():
                    csq = int(L.strip())
            # also accept native AT+CSQ reply like 15,99
            for L in lines:
                m = re.match(r"^(\d{1,2}),\d+", L.strip())
                if m:
                    csq = int(m.group(1))
            log("TEST", f"CSQ_PARSE={csq}")
            if csq is not None and csq != 99 and csq > 0:
                log("TEST", f"ATTACH_OK csq={csq}")
                return True
            # passive listen for attach breadcrumbs
            more = read_lines(3.0)
            blob = "\n".join(more)
            if "Failed to send" in blob or "Opened the MQTT" in blob:
                log("TEST", "ATTACH_VIA_UPLINK_ACTIVITY")
                return True
            if "registered" in blob.lower() or "PDP" in blob:
                log("TEST", "ATTACH_HINT in logs")
        return False

    def qval(cmd: str) -> str:
        send(PIN, 0.35)
        for L in send(cmd, 2.0):
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
                or "Signal" in t
                or "NBIOT" in t
                or "Echo" in t
                or "IMEI" in t
                or "IMSI" in t
                or "APN" in t
                or "Frequency" in t
                or "Configure Network" in t
                or "data format" in t
                or "Model information" in t
                or t.startswith("AT+PWR")
            ):
                continue
            return t
        return ""

    def apply() -> None:
        for cmd, w in [
            ("AT+PRO=3,5", 1.8),
            ("AT+TLSMOD=0,0", 1.2),
            (f"AT+SERVADDR={IP},{PORT}", 1.5),
            (f"AT+BKDNS=1,0,{IP},{PORT}", 1.5),
            (f"AT+CLIENT={CLIENT}", 1.2),
            (f"AT+UNAME={USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
            ("AT+MQOS=1", 1.1),
            (f"AT+SERVADDR={IP},{PORT}", 1.3),
            (f"AT+BKDNS=1,0,{IP},{PORT}", 1.3),
        ]:
            send(PIN, 0.3)
            send(cmd, w)

    def verify() -> dict[str, str]:
        out = {}
        for k, cmd in [
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("PRO", "AT+PRO=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("UNAME", "AT+UNAME=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("SUBTOPIC", "AT+SUBTOPIC=?"),
            ("TLSMOD", "AT+TLSMOD=?"),
        ]:
            out[k] = qval(cmd)
            log("CFG", f"{k}={out[k]}")
        return out

    if not unlock():
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    # Drain init first
    read_lines(8.0)
    attached = wait_attach(240)
    log("TEST", f"attached={attached}")

    # Quiet 5s then apply
    read_lines(5.0)
    send(PIN, 0.5)
    apply()
    cfg = verify()
    serv_ok = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")
    log(
        "TEST",
        f"VERIFY serv={serv_ok} pro={cfg.get('PRO')} client={cfg.get('CLIENT')} "
        f"uname={cfg.get('UNAME')} pub={cfg.get('PUBTOPIC')} bkdns={cfg.get('BKDNS')}",
    )

    # Re-apply SERVADDR/BKDNS once more if needed
    if not serv_ok:
        send(PIN, 0.3)
        send(f"AT+SERVADDR={IP},{PORT}", 1.5)
        send(PIN, 0.3)
        send(f"AT+BKDNS=1,0,{IP},{PORT}", 1.5)
        cfg = verify()
        serv_ok = IP in cfg.get("SERVADDR", "") and PORT in cfg.get("SERVADDR", "")

    uplink_t0 = datetime.now(timezone.utc).isoformat()
    print("\n>>> HOLD ACT 1-3s to TRIGGER UPLINK <<<\n", flush=True)
    log("TEST", f"LISTEN_UPLINK 200s t0={uplink_t0}")
    flags = dict(opened=False, connected=False, upload=False, failed=False, open_fail=False)
    domain = ""
    end = time.time() + 200
    while time.time() < end:
        for L in read_lines(1.0):
            if "Domain IP" in L or "Connecting" in L or "server" in L.lower():
                domain = L
                log("MARK", f"NET {L[:100]}")
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
        if flags["upload"] or flags["connected"]:
            read_lines(5.0)
            break

    fail_t = datetime.now(timezone.utc).isoformat()
    if flags["failed"] or not (flags["upload"] or flags["connected"]):
        log("TEST", f"DIAG t={fail_t}")
        send(PIN, 0.5)
        for cmd in (
            "AT+SERVADDR=?",
            "AT+BKDNS=?",
            "AT+CSQ=?",
            "AT+CIMI=?",
            "AT+CGPADDR=?",
            "AT+CCID=?",
            "AT+CLIENT=?",
            "AT+UNAME=?",
            "AT+PUBTOPIC=?",
            "AT+PRO=?",
            "AT+TLSMOD=?",
        ):
            send(cmd, 1.5)

    print("=== SUMMARY ===", flush=True)
    print(f"attached={attached}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"domain={domain}", flush=True)
    print(f"uplink_t0={uplink_t0}", flush=True)
    print(f"fail_or_end_t={fail_t}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["connected"] or flags["upload"] else 1)


if __name__ == "__main__":
    main()
