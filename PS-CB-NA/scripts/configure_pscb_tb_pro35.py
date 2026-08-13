#!/usr/bin/env python3
"""Configure PS-CB-NA for Railway MQTT (AT+PRO=3,5), reboot-check SERVADDR, monitor uplinks."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
from monitor import load_dotenv, resolve_pin  # noqa: E402
from railway_mqtt import load_config, servaddr  # noqa: E402

PORT = "COM8"
BAUD = 9600
DEVICE_ID = "ps-cb"
TDC = 180
MONITOR_CYCLES = 3
MONITOR_MAX_S = 14 * 60
LOG_DIR = ROOT / "logs"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pin = resolve_pin("")
    if not pin:
        print("ERROR: No DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    cfg = load_config()
    mqtt_pass = cfg["MQTT_PASS"]
    if not mqtt_pass:
        print("ERROR: MQTT_PASS missing (railway-mqtt.local.env)", file=sys.stderr)
        return 2

    # Prefer IP SERVADDR (proven on this unit / carrier); BKDNS pins same IP.
    addr = servaddr(cfg, use_ip=True)  # 66.33.22.220,33239
    host_name = f"{cfg['MQTT_HOST']},{cfg['MQTT_PORT']}"  # altaria.proxy.rlwy.net,33239
    user = cfg["MQTT_USER"]
    pub = f"dragino/{DEVICE_ID}/up"
    sub = f"dragino/{DEVICE_ID}/down"
    bkdns = f"1,0,{cfg['MQTT_FALLBACK_IP']},{cfg['MQTT_PORT']}"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpath = LOG_DIR / f"{stamp}_pscb_railway_pro35.raw.log"
    print(f"Log: {logpath}", flush=True)
    print(f"Railway broker SERVADDR={addr} (proxy {host_name})", flush=True)

    try:
        ser = serial.Serial(PORT, BAUD, timeout=0.25, write_timeout=2)
    except serial.SerialException as e:
        print(f"ERROR opening {PORT}: {e}", file=sys.stderr)
        print("Wake/RESET device or free COM8, then retry.", file=sys.stderr)
        return 3

    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass

    buf = bytearray()
    summary: dict = {
        "broker": {"servaddr": addr, "proxy": host_name, "user": user, "pub": pub, "sub": sub},
        "pre": {},
        "post_apply": {},
        "post_reboot": {},
        "cycles": [],
        "applied": [],
    }

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***").replace(mqtt_pass, "***PASS***")
        row = f"{utc()} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def drain(seconds: float) -> list[str]:
        end = time.monotonic() + seconds
        got: list[str] = []
        while time.monotonic() < end:
            chunk = ser.read(4096)
            if chunk:
                buf.extend(chunk)
                while True:
                    i = buf.find(b"\n")
                    if i < 0:
                        break
                    raw = bytes(buf[: i + 1])
                    del buf[: i + 1]
                    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        log("RX", text)
                        got.append(text)
            else:
                time.sleep(0.02)
        return got

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        shown = cmd
        if cmd.strip() == pin or cmd.strip() == f"AT+PIN={pin}":
            shown = "***PIN***"
        elif cmd.startswith("AT+PWD=") and mqtt_pass in cmd:
            shown = "AT+PWD=***PASS***"
        log("TX", shown)
        ser.write((cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
        ser.flush()
        return drain(wait)

    def unlock(attempts: int = 16) -> bool:
        for i in range(1, attempts + 1):
            got = send(pin, 2.0)
            if any("Password Correct" in g for g in got):
                log("SYS", f"unlock_ok attempt={i}")
                return True
            send(f"AT+PIN={pin}", 1.2)
            at = send("AT", 1.2)
            if any(g.strip().upper() == "OK" for g in at):
                log("SYS", f"AT_OK attempt={i}")
                return True
            time.sleep(0.2)
        return False

    def query_key(cmd: str, wait: float = 1.8) -> str:
        for L in send(cmd, wait):
            t = L.strip()
            if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                continue
            if "Password" in t or "password" in t:
                continue
            return t
        return ""

    def query_mqtt() -> dict[str, str]:
        keys = [
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
            ("mqos", "AT+MQOS=?"),
        ]
        out: dict[str, str] = {}
        for k, c in keys:
            out[k] = query_key(c)
        return out

    def wait_idle(max_s: float = 50.0) -> None:
        log("SYS", f"WAIT_IDLE {max_s}s")
        deadline = time.monotonic() + max_s
        quiet_since = None
        while time.monotonic() < deadline:
            lines = drain(1.0)
            for L in lines:
                if "Upload start" in L:
                    quiet_since = None
                if "power-off successful" in L or "End of upload" in L:
                    quiet_since = time.monotonic()
            if quiet_since and time.monotonic() - quiet_since >= 6:
                log("SYS", "IDLE_OK")
                return
            if not lines and quiet_since is None:
                if not drain(3.0):
                    log("SYS", "IDLE_QUIET")
                    return
        log("SYS", "IDLE_TIMEOUT")

    def is_railway(serv: str) -> bool:
        s = (serv or "").lower()
        return ("66.33.22.220" in s and "33239" in s) or (
            "altaria.proxy.rlwy.net" in s and "33239" in s
        )

    drain(1.0)
    wait_idle(45.0)
    if not unlock():
        log("SYS", "UNLOCK_FAILED")
        ser.close()
        return 4

    summary["pre"] = query_mqtt()
    log("SYS", f"PRE {summary['pre']}")

    cmds = [
        ("AT+PRO=3,5", 2.5),
        ("AT+TLSMOD=0,0", 1.8),
        (f"AT+SERVADDR={addr}", 2.5),
        (f"AT+BKDNS={bkdns}", 2.5),
        (f"AT+CLIENT={DEVICE_ID}", 2.0),
        (f"AT+UNAME={user}", 1.8),
        (f"AT+PWD={mqtt_pass}", 1.8),
        (f"AT+PUBTOPIC={pub}", 1.8),
        (f"AT+SUBTOPIC={sub}", 1.8),
        ("AT+MQOS=1", 1.5),
        (f"AT+TDC={TDC}", 1.5),
        # re-assert after PRO
        (f"AT+SERVADDR={addr}", 2.0),
        (f"AT+BKDNS={bkdns}", 2.0),
        (f"AT+CLIENT={DEVICE_ID}", 1.8),
        (f"AT+UNAME={user}", 1.5),
        (f"AT+PWD={mqtt_pass}", 1.5),
    ]
    for cmd, w in cmds:
        shown = cmd if mqtt_pass not in cmd else cmd.replace(mqtt_pass, "***PASS***")
        summary["applied"].append(shown)
        send(cmd, w)

    unlock(8)
    send("AT+CFG", 10.0)
    summary["post_apply"] = query_mqtt()
    log("SYS", f"POST_APPLY {summary['post_apply']}")

    if not is_railway(summary["post_apply"].get("servaddr", "")):
        log("SYS", "WARN not Railway — reassert")
        unlock(4)
        send(f"AT+SERVADDR={addr}", 2.0)
        send(f"AT+BKDNS={bkdns}", 2.0)
        send(f"AT+CLIENT={DEVICE_ID}", 1.8)
        send(f"AT+UNAME={user}", 1.5)
        send(f"AT+PWD={mqtt_pass}", 1.5)
        send(f"AT+PUBTOPIC={pub}", 1.5)
        send(f"AT+SUBTOPIC={sub}", 1.5)
        summary["post_apply"] = query_mqtt()
        log("SYS", f"POST_APPLY_RETRY {summary['post_apply']}")

    # reboot persistence
    log("SYS", "REBOOT ATZ")
    send("ATZ", 3.0)
    drain(28.0)
    ok = unlock(22)
    log("SYS", f"post_reboot_unlock={ok}")
    if not ok:
        log("SYS", "POST_REBOOT_UNLOCK_FAILED")
        ser.close()
        _print_summary(summary, logpath)
        return 5

    summary["post_reboot"] = {
        "servaddr": query_key("AT+SERVADDR=?"),
        "pro": query_key("AT+PRO=?"),
        "bkdns": query_key("AT+BKDNS=?"),
        "client": query_key("AT+CLIENT=?"),
        "uname": query_key("AT+UNAME=?"),
        "pub": query_key("AT+PUBTOPIC=?"),
    }
    survived = is_railway(summary["post_reboot"].get("servaddr", ""))
    summary["servaddr_survived_reboot"] = survived
    log("SYS", f"POST_REBOOT {summary['post_reboot']} SURVIVED={survived}")

    # if reboot wiped Railway, restore once more without another ATZ
    if not survived:
        unlock(4)
        for cmd, w in [
            (f"AT+SERVADDR={addr}", 2.0),
            (f"AT+BKDNS={bkdns}", 2.0),
            (f"AT+CLIENT={DEVICE_ID}", 1.8),
            (f"AT+UNAME={user}", 1.5),
            (f"AT+PWD={mqtt_pass}", 1.5),
            (f"AT+PUBTOPIC={pub}", 1.5),
            (f"AT+SUBTOPIC={sub}", 1.5),
            ("AT+PRO=3,5", 2.0),
            (f"AT+SERVADDR={addr}", 2.0),
        ]:
            send(cmd, w)
        summary["post_reboot"] = {
            "servaddr": query_key("AT+SERVADDR=?"),
            "pro": query_key("AT+PRO=?"),
            "bkdns": query_key("AT+BKDNS=?"),
        }
        summary["servaddr_survived_reboot"] = False
        summary["restored_after_reboot"] = is_railway(summary["post_reboot"].get("servaddr", ""))
        log("SYS", f"RESTORED_AFTER_REBOOT {summary['post_reboot']}")

    send("AT+DEBUG=1", 1.5)

    log("SYS", f"MONITOR up to {MONITOR_MAX_S}s for {MONITOR_CYCLES} cycles")
    deadline = time.monotonic() + MONITOR_MAX_S
    cycle: dict | None = None
    cycles_done = 0

    while time.monotonic() < deadline and cycles_done < MONITOR_CYCLES:
        for L in drain(1.0):
            low = L.lower()
            if "start of upload" in low or "upload start" in low:
                if cycle and cycle.get("end") is None:
                    cycle["end"] = "interrupted"
                    summary["cycles"].append(cycle)
                    cycles_done = len(summary["cycles"])
                cycle = {
                    "n": cycles_done + 1,
                    "start_ts": utc(),
                    "upload_ok": False,
                    "failed_to_send": False,
                    "connected": False,
                    "subscribe_ok": False,
                    "domain_ip": "",
                    "notes": [],
                }
                log("MARK", f"CYCLE_START n={cycle['n']}")
            if cycle is None:
                continue
            if "successfully connected" in low:
                cycle["connected"] = True
            if "domain ip" in low:
                cycle["domain_ip"] = L.split(":", 1)[-1].strip() if ":" in L else L
            if "upload data successfully" in low:
                cycle["upload_ok"] = True
            if "failed to send" in low:
                cycle["failed_to_send"] = True
            if "subscribe" in low and ("success" in low or "ok" in low):
                cycle["subscribe_ok"] = True
            if "not authori" in low:
                cycle["notes"].append("auth_fail")
            if "hivemq" in low or "167.235.104.181" in L:
                cycle["notes"].append(f"wrong_broker:{L[:80]}")
            if "end of upload" in low or "power-off successful" in low:
                cycle["end"] = utc()
                summary["cycles"].append(cycle)
                cycles_done = len(summary["cycles"])
                log(
                    "MARK",
                    f"CYCLE_END n={cycle['n']} upload_ok={cycle['upload_ok']} "
                    f"failed={cycle['failed_to_send']} connected={cycle['connected']} "
                    f"sub={cycle['subscribe_ok']} ip={cycle['domain_ip']}",
                )
                cycle = None
                if cycles_done >= MONITOR_CYCLES:
                    break

    if cycle is not None:
        cycle["end"] = "timeout"
        summary["cycles"].append(cycle)

    unlock(6)
    summary["final"] = {
        "servaddr": query_key("AT+SERVADDR=?"),
        "pro": query_key("AT+PRO=?"),
        "bkdns": query_key("AT+BKDNS=?"),
        "client": query_key("AT+CLIENT=?"),
        "uname": query_key("AT+UNAME=?"),
        "pub": query_key("AT+PUBTOPIC=?"),
    }
    log("SYS", f"FINAL {summary['final']}")
    ser.close()
    _print_summary(summary, logpath)
    return 0


def _print_summary(summary: dict, logpath: Path) -> None:
    print("=== SUMMARY ===", flush=True)
    print(f"BROKER={summary.get('broker')}", flush=True)
    print(f"PRE={summary.get('pre')}", flush=True)
    print(f"POST_APPLY={summary.get('post_apply')}", flush=True)
    print(f"POST_REBOOT={summary.get('post_reboot')}", flush=True)
    print(f"SERVADDR_SURVIVED={summary.get('servaddr_survived_reboot')}", flush=True)
    print(f"FINAL={summary.get('final')}", flush=True)
    print(f"APPLIED={summary.get('applied')}", flush=True)
    for c in summary.get("cycles") or []:
        print(f"CYCLE={c}", flush=True)
    print(f"LOG={logpath}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
