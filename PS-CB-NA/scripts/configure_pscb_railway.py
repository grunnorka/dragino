#!/usr/bin/env python3
"""Configure PS-CB-NA for Railway MQTT (CLIENT=ps-cb) and wait for one uplink."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]

HOST_IP = "66.33.22.220"
PORT = 33239
USER = "dragino"
CLIENT = "ps-cb"
PUB = "dragino/ps-cb/up"
SUB = "dragino/ps-cb/down"
LISTEN_S = 360


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
    load_env(ROOT / ".env")
    load_env(ROOT / "railway-mqtt.local.env")
    pin = os.environ["DRAGINO_PIN"].strip()
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    uart_port = os.environ.get("DRAGINO_PORT", "COM8").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_pscb_railway_config.raw.log"
    logpath.parent.mkdir(exist_ok=True)
    ser = serial.Serial(uart_port, 9600, timeout=0.2)
    print(f"Opened {uart_port}; log={logpath}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(mqtt_pass, "***").replace(pin, "***PIN***")
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

    def unlock(n: int = 14) -> bool:
        for _ in range(n):
            if any("Password Correct" in L for L in send(pin, 2.0)):
                return True
            send(f"AT+PIN={pin}", 1.2)
            time.sleep(0.25)
        return False

    def wait_idle(max_s: float = 100.0) -> None:
        log("TEST", f"WAIT_IDLE {max_s}s")
        deadline = time.time() + max_s
        quiet_since = None
        while time.time() < deadline:
            lines = read_lines(1.0)
            for L in lines:
                if "Upload start" in L:
                    quiet_since = None
                if "power-off successful" in L or "End of upload" in L:
                    quiet_since = time.time()
            if quiet_since and time.time() - quiet_since >= 6:
                log("TEST", "IDLE_OK")
                return
            if not lines and quiet_since is None:
                if not read_lines(4.0):
                    log("TEST", "IDLE_QUIET")
                    return
        log("TEST", "IDLE_TIMEOUT")

    def query() -> dict[str, str]:
        info: dict[str, str] = {}
        for key, cmd in [
            ("servaddr", "AT+SERVADDR=?"),
            ("bkdns", "AT+BKDNS=?"),
            ("pro", "AT+PRO=?"),
            ("client", "AT+CLIENT=?"),
            ("uname", "AT+UNAME=?"),
            ("pwd", "AT+PWD=?"),
            ("pub", "AT+PUBTOPIC=?"),
            ("sub", "AT+SUBTOPIC=?"),
            ("tls", "AT+TLSMOD=?"),
            ("tdc", "AT+TDC=?"),
        ]:
            for L in send(cmd, 1.8):
                t = L.strip()
                if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                    continue
                info[key] = t
                break
        return info

    def apply() -> None:
        unlock()
        cmds = [
            ("AT+PRO=3,5", 2.5),
            ("AT+TLSMOD=0,0", 2.0),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 2.5),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 2.5),
            (f"AT+CLIENT={CLIENT}", 2.0),
            (f"AT+UNAME={USER}", 1.8),
            (f"AT+PWD={mqtt_pass}", 1.8),
            (f"AT+PUBTOPIC={PUB}", 1.8),
            (f"AT+SUBTOPIC={SUB}", 1.8),
            ("AT+MQOS=1", 1.5),
            ("AT+TDC=180", 1.5),
        ]
        for cmd, w in cmds:
            send(cmd, w)
        unlock()
        for cmd, w in [
            (f"AT+CLIENT={CLIENT}", 2.0),
            (f"AT+UNAME={USER}", 1.8),
            (f"AT+PWD={mqtt_pass}", 1.8),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 2.0),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 2.0),
            (f"AT+PUBTOPIC={PUB}", 1.5),
            (f"AT+SUBTOPIC={SUB}", 1.5),
            ("AT+TLSMOD=0,0", 1.5),
            ("AT+PRO=3,5", 2.0),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 2.0),
        ]:
            send(cmd, w)

    read_lines(1.0)
    wait_idle(90.0)
    ok = unlock()
    log("TEST", f"unlock={ok}")
    if not ok:
        log("TEST", "UNLOCK_FAILED abort")
        ser.close()
        raise SystemExit(2)

    apply()
    send("AT+CFG", 10.0)
    cfg = query()
    log("TEST", f"CFG {cfg}")

    hive = "hivemq" in str(cfg).lower()
    serv = cfg.get("servaddr", "")
    bk = cfg.get("bkdns", "")
    good_addr = HOST_IP in serv and str(PORT) in serv
    good_bk = HOST_IP in bk and str(PORT) in bk
    client_ok = cfg.get("client") == CLIENT
    uname_ok = cfg.get("uname") == USER
    log(
        "TEST",
        f"VERIFY hive={hive} addr_ok={good_addr} bk_ok={good_bk} "
        f"client_ok={client_ok} uname_ok={uname_ok}",
    )
    if hive or not good_addr:
        log("TEST", "WARN bad broker — reassert SERVADDR/BKDNS")
        unlock()
        send(f"AT+SERVADDR={HOST_IP},{PORT}", 2.0)
        send(f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 2.0)
        cfg = query()
        log("TEST", f"CFG_RETRY {cfg}")

    if not client_ok or "null" in (cfg.get("client") or "").lower():
        unlock()
        send(f"AT+CLIENT={CLIENT}", 2.0)
        send(f"AT+UNAME={USER}", 1.8)
        send(f"AT+PWD={mqtt_pass}", 1.8)
        cfg = query()
        log("TEST", f"CFG_AUTH {cfg}")

    # Wait for one successful uplink
    deadline = time.time() + LISTEN_S
    log("TEST", f"LISTEN uplink up to {LISTEN_S}s")
    success = False
    connected = False
    markers: list[str] = []
    while time.time() < deadline:
        for L in read_lines(1.0):
            if "Successfully connected to the server" in L:
                connected = True
                markers.append(L)
            if "Upload data successfully" in L:
                success = True
                markers.append(L)
                log("MARK", "UPLINK_SUCCESS")
                # collect a bit more (subscribe / end)
                read_lines(20.0)
                deadline = 0
                break
            if "not authorised" in L.lower() or "not authorized" in L.lower():
                markers.append(L)
                log("MARK", "AUTH_FAIL_SEEN")
            if "Domain IP" in L or "Opened the MQTT" in L or "Failed to send" in L:
                markers.append(L)

    final = query()
    log("TEST", f"FINAL_CFG {final}")
    print("=== SUMMARY ===", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"FINAL={final}", flush=True)
    print(f"connected={connected} upload_success={success}", flush=True)
    print(f"markers={markers[-12:]}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    print("DONE", flush=True)
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
