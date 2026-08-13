#!/usr/bin/env python3
"""A/B: LTC2 -> same Railway TCP proxy as PS-CB (altaria:33239 / 66.33.22.220).

Quiet-window config only; listen-only during uplink (no AT spam mid-connect).
TDC 60 preferred, else 120. AFK multi-cycle.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
PIN = "358613"
COM, BAUD = "COM8", 9600
# Same endpoint PS-CB uses successfully
IP, PORT = "66.33.22.220", "33239"
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
LISTEN_S = 900  # enough for several 60/120s cycles
MAX_FAILS = 6


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
    # Load file into a local map so polluted shell MQTT_PORT=24233 cannot win.
    file_env: dict[str, str] = {}
    env_path = ROOT / "railway-mqtt.local.env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            file_env[k.strip()] = v.strip().strip('"').strip("'")
    load_env(env_path)
    global IP, PORT
    # Force PS-CB public TCP proxy (never hayabusa / 24233 for this A/B)
    IP = file_env.get("MQTT_FALLBACK_IP", IP).strip()
    PORT = file_env.get("MQTT_PORT", PORT).strip()
    if PORT == "24233" or "223" in IP:
        IP, PORT = "66.33.22.220", "33239"
    mqtt_pass = file_env.get("MQTT_PASS") or os.environ["MQTT_PASS"]
    mqtt_pass = mqtt_pass.strip()
    assert PORT == "33239" and IP == "66.33.22.220", (IP, PORT)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_altaria_ab.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}; A/B target={IP},{PORT} (PS-CB path); log={logpath}", flush=True)
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

    def unlock(timeout: float = 2100.0) -> bool:
        """Wait for UART wake (upload cycle or residual session). Avoid hammering."""
        log("TEST", f"UNLOCK_WAIT {timeout}s (passive listen + light probe)")
        deadline = time.time() + timeout
        next_probe = 0.0
        while time.time() < deadline:
            lines = read_lines(1.0)
            woke = any(
                any(
                    x in L
                    for x in (
                        "Upload start",
                        "Password",
                        "LTC2-CB",
                        "MQTT",
                        "NB module",
                        "DNS",
                        "BAT:",
                    )
                )
                for L in lines
            )
            if woke or time.time() >= next_probe:
                next_probe = time.time() + (2.0 if woke else 8.0)
                if any("Password Correct" in L for L in send(PIN, 0.85)):
                    log("TEST", "UNLOCK_OK")
                    return True
                if any("LTC2-CB" in L for L in send("AT+MODEL=?", 0.95)):
                    log("TEST", "UNLOCK_OK already")
                    return True
        return False

    def wait_post_upload_quiet(max_wait: float = 120.0) -> None:
        """Wait until after End of upload / NB power-off, then 5s silence."""
        log("TEST", "WAIT_POST_UPLOAD_QUIET")
        end = time.time() + max_wait
        saw_end = False
        streak = 0
        while time.time() < end:
            lines = read_lines(1.0)
            if any("End of upload" in L or "NB module power-off" in L for L in lines):
                saw_end = True
                streak = 0
                continue
            noisy = any(
                any(x in L for x in ("MQTT", "Upload", "Failed", "TCP", "Connecting", "Domain", "DNS"))
                for L in lines
            )
            if saw_end or not lines:
                streak = 0 if noisy else streak + 1
            else:
                streak = 0 if noisy else streak
            if streak >= 5:
                log("TEST", f"QUIET_OK saw_end={saw_end} streak={streak}")
                return
        log("TEST", f"QUIET_TIMEOUT saw_end={saw_end} streak={streak}")

    def qval(cmd: str) -> str:
        send(PIN, 0.3)
        for L in send(cmd, 1.7):
            t = L.strip()
            if (
                not t
                or t == "OK"
                or t.startswith("AT+")
                or t.startswith("[")
                or any(
                    x in t
                    for x in (
                        "Password",
                        "Attention",
                        "Failed",
                        "MQTT",
                        "TCP",
                        "Upload",
                        "Domain",
                        "Closing",
                        "module",
                        "Signal",
                        "NBIOT",
                        "Echo",
                        "IMEI",
                        "IMSI",
                        "APN",
                        "Configure",
                        "data format",
                        "Model information",
                    )
                )
                or t.startswith("AT+PWR")
            ):
                continue
            return t
        return ""

    def apply_core() -> None:
        for cmd, w in [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={IP},{PORT}", 1.7),
            (f"AT+BKDNS=1,0,{IP},{PORT}", 1.7),
            (f"AT+CLIENT={CLIENT}", 1.2),
            (f"AT+UNAME={USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
            ("AT+MQOS=1", 1.1),
            # re-assert after PRO side effects
            (f"AT+SERVADDR={IP},{PORT}", 1.5),
            (f"AT+BKDNS=1,0,{IP},{PORT}", 1.5),
            (f"AT+CLIENT={CLIENT}", 1.1),
            (f"AT+UNAME={USER}", 1.1),
            (f"AT+PWD={mqtt_pass}", 1.1),
            (f"AT+PUBTOPIC={PUB}", 1.1),
            (f"AT+SUBTOPIC={SUB}", 1.1),
        ]:
            send(PIN, 0.3)
            send(cmd, w)

    def set_tdc() -> int:
        for val in (60, 120):
            send(PIN, 0.3)
            send(f"AT+TDC={val}", 1.6)
            time.sleep(0.3)
            got = qval("AT+TDC=?")
            log("TEST", f"TDC_READBACK={got!r} want={val}")
            if str(val) == got or got == str(val):
                return val
            # some FW echo only digits amid noise — accept containment if exact-ish
            if got.strip() == str(val):
                return val
        # last try: set 60 twice
        send(PIN, 0.3)
        send("AT+TDC=60", 1.6)
        send(PIN, 0.3)
        send("AT+TDC=60", 1.6)
        got = qval("AT+TDC=?")
        log("TEST", f"TDC_FINAL={got!r}")
        if got == "60":
            return 60
        if got == "120":
            return 120
        return int(got) if got.isdigit() else -1

    def verify() -> dict[str, str]:
        out: dict[str, str] = {}
        for k, cmd in [
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("PRO", "AT+PRO=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("UNAME", "AT+UNAME=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("SUBTOPIC", "AT+SUBTOPIC=?"),
            ("TLSMOD", "AT+TLSMOD=?"),
            ("TDC", "AT+TDC=?"),
        ]:
            out[k] = qval(cmd)
            log("CFG", f"{k}={out[k]}")
        return out

    def cfg_ok(cfg: dict[str, str]) -> bool:
        blob = str(cfg).lower()
        return (
            IP in cfg.get("SERVADDR", "")
            and PORT in cfg.get("SERVADDR", "")
            and "3,5" in cfg.get("PRO", "")
            and cfg.get("CLIENT") == CLIENT
            and cfg.get("UNAME") == USER
            and PUB in cfg.get("PUBTOPIC", "")
            and "0,0" in cfg.get("TLSMOD", "")
            and "hivemq" not in blob
            and "24233" not in cfg.get("SERVADDR", "")
        )

    def quiet_diag() -> None:
        """Short diag only after End of upload — never during connect."""
        log("TEST", "QUIET_DIAG")
        send(PIN, 0.4)
        for cmd in (
            "AT+SERVADDR=?",
            "AT+BKDNS=?",
            "AT+CSQ=?",
            "AT+CGPADDR=?",
            "AT+CIMI=?",
            "AT+TDC=?",
            "AT+CLIENT=?",
            "AT+PUBTOPIC=?",
        ):
            send(cmd, 1.4)

    if not unlock(2100):
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        raise SystemExit(2)

    # UART sleeps in seconds — apply IMMEDIATELY (no quiet-wait before config).
    send(PIN, 0.4)
    tdc = set_tdc()
    apply_core()
    cfg = verify()
    ok = cfg_ok(cfg)
    log("TEST", f"VERIFY={'PASS' if ok else 'FAIL'} TDC={tdc} SERVADDR={cfg.get('SERVADDR')} BKDNS={cfg.get('BKDNS')}")

    if not ok:
        if not any("LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
            send(PIN, 0.8)
        send(PIN, 0.4)
        if tdc not in (60, 120):
            tdc = set_tdc()
        apply_core()
        send(PIN, 0.3)
        for L in send("AT+CFG", 8.0):
            if L.startswith("AT+") and "=" in L:
                k, _, v = L.partition("=")
                key = k.replace("AT+", "")
                cfg[key] = v
                log("CFG", f"{key}={v}")
        ok = cfg_ok(cfg)
        log("TEST", f"VERIFY2={'PASS' if ok else 'FAIL'} cfg={cfg}")

    # LISTEN ONLY — no AT during uplink windows
    log("TEST", f"LISTEN_ONLY budget={LISTEN_S}s target={IP},{PORT}")
    flags = {
        "opened": 0,
        "connected": 0,
        "upload": 0,
        "failed": 0,
        "open_fail": 0,
        "cycles": 0,
        "domain": "",
        "auth_fail": 0,
    }
    end = time.time() + LISTEN_S
    in_upload = False
    last_fail_diag = 0.0

    while time.time() < end and flags["upload"] == 0:
        for L in read_lines(1.0):
            if "Upload start" in L:
                flags["cycles"] += 1
                in_upload = True
                log("MARK", f"CYCLE n={flags['cycles']} LISTEN_ONLY")
            if "Domain IP" in L or "Connecting" in L:
                flags["domain"] = L
                log("MARK", f"NET {L[:140]}")
            if "Opened the MQTT" in L:
                flags["opened"] += 1
                log("MARK", "OPENED")
            if "Failed to open the MQTT" in L:
                flags["open_fail"] += 1
                log("MARK", "OPEN_FAIL")
            if "Successfully connected to the server" in L:
                flags["connected"] += 1
                log("MARK", "CONNECTED")
            if "Upload data successfully" in L:
                flags["upload"] += 1
                log("MARK", "UPLOAD_OK")
                end = 0
                break
            if "not authorised" in L.lower() or "not authorized" in L.lower():
                flags["auth_fail"] += 1
                log("MARK", "AUTH_FAIL")
            if "Failed to send" in L:
                flags["failed"] += 1
                log("MARK", f"FAILED_SEND n={flags['failed']}")
            if "End of upload" in L:
                in_upload = False
                # after fail, quiet diag + optional re-pin once per fail
                if flags["failed"] > 0 and time.time() - last_fail_diag > 30:
                    last_fail_diag = time.time()
                    read_lines(10.0)  # let NB power-off finish
                    send(PIN, 0.5)
                    quiet_diag()
                    # re-pin SERVADDR only in quiet (not mid-cycle)
                    send(PIN, 0.3)
                    send(f"AT+SERVADDR={IP},{PORT}", 1.5)
                    send(PIN, 0.3)
                    send(f"AT+BKDNS=1,0,{IP},{PORT}", 1.5)
                    if tdc not in (60, 120):
                        tdc = set_tdc()
                    cfg = verify()
                    log("TEST", f"REPIN cfg_ok={cfg_ok(cfg)} SERVADDR={cfg.get('SERVADDR')} TDC={cfg.get('TDC')}")
            # never send AT while in_upload
            _ = in_upload
        if flags["failed"] >= MAX_FAILS and flags["upload"] == 0:
            log("TEST", "MAX_FAILS")
            break

    # final snapshot if possible
    send(PIN, 0.4)
    if any("Password Correct" in L or "LTC2-CB" in L for L in send("AT+MODEL=?", 1.0)):
        cfg = verify()

    print("=== SUMMARY ===", flush=True)
    print(f"target={IP},{PORT}", flush=True)
    print(f"tdc={tdc}", flush=True)
    print(f"cfg={cfg}", flush=True)
    print(f"flags={flags}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"UTC_END={datetime.now(timezone.utc).isoformat()}", flush=True)
    ser.close()
    raise SystemExit(0 if flags["upload"] or flags["connected"] else 1)


if __name__ == "__main__":
    main()
