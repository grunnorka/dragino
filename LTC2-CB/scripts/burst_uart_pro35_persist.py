#!/usr/bin/env python3
"""UART burst: shared burst unlock, then Railway PRO=3,5 apply before bootloader reset."""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from dragino_uart import (  # noqa: E402
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    send_line,
    unlock,
)
from railway_mqtt import load_config  # noqa: E402

COM, BAUD = "COM8", 9600
USER, CLIENT = "dragino", "ltc2"
PUB, SUB = "dragino/ltc2/up", "dragino/ltc2/down"
TDC = 120


def main() -> int:
    load_dotenv(ROOT / "railway-mqtt.local.env")
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    mqtt_pass = cfg["MQTT_PASS"].strip()
    if not mqtt_pass:
        print("ERROR: MQTT_PASS missing", flush=True)
        return 5
    host_ip = cfg.get("MQTT_FALLBACK_IP", "66.33.22.220").strip()
    port = int(cfg.get("MQTT_PORT", "33239"))
    pin = resolve_pin("", device="ltc2")
    if not pin:
        print(
            "ERROR: missing LTC2 PIN (DRAGINO_PIN_LTC2 / sensorInfo.txt / DRAGINO_PIN)",
            flush=True,
        )
        return 5

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_burst_uart_pro35.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = open_serial(COM, BAUD, timeout=0.15)
    buf = LineBuffer()

    def log(tag: str, s: str) -> None:
        safe = s.replace(mqtt_pass, "***").replace(pin, "***PIN***")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(sec: float) -> list[str]:
        return read_for(ser, sec, buf, lambda L: log("RX", L))

    def send(cmd: str, w: float = 0.55) -> list[str]:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        log("TX", shown)
        send_line(ser, cmd)
        return read_lines(w)

    print(
        f"\nTarget {host_ip},{port} PRO=3,5 topic={PUB}\n"
        ">>> PRESS ACT 1-3s NOW (and keep SW1=Flash) <<<\n",
        flush=True,
    )
    log("TEST", f"burst unlock via dragino_uart; log={logpath}")

    result = unlock(
        ser,
        pin,
        policy="burst",
        timeout=540.0,
        confirm_model="LTC2-CB",
        on_line=lambda L: log("RX", L),
        on_tx=lambda L: log("TX", L),
    )
    if not result.ok:
        log("TEST", f"UNLOCK_FAIL {result.hint}")
        print(f"BLOCKED: {result.hint}", flush=True)
        ser.close()
        return 2

    unlocked = True
    log("TEST", "UNLOCK_OK — apply ASAP")
    before: dict[str, str] = {}
    for key, q in [
        ("PRO", "AT+PRO=?"),
        ("SERVADDR", "AT+SERVADDR=?"),
        ("TDC", "AT+TDC=?"),
        ("MODEL", "AT+MODEL=?"),
        ("CSQ", "AT+CSQ"),
    ]:
        for L in send(q, 0.7):
            t = L.strip()
            if t and t != "OK" and not t.startswith("AT+") and not t.startswith("["):
                before[key] = t
                break
    log("TEST", f"BEFORE {before}")

    for cmd, w in [
        ("AT+PRO=3,5", 0.7),
        ("AT+TLSMOD=0,0", 0.5),
        (f"AT+SERVADDR={host_ip},{port}", 0.7),
        (f"AT+BKDNS=1,0,{host_ip},{port}", 0.7),
        (f"AT+CLIENT={CLIENT}", 0.5),
        (f"AT+UNAME={USER}", 0.5),
        (f"AT+PWD={mqtt_pass}", 0.5),
        (f"AT+PUBTOPIC={PUB}", 0.5),
        (f"AT+SUBTOPIC={SUB}", 0.5),
        ("AT+MQOS=1", 0.4),
        (f"AT+TDC={TDC}", 0.5),
        (f"AT+SERVADDR={host_ip},{port}", 0.6),
        (f"AT+BKDNS=1,0,{host_ip},{port}", 0.6),
        (f"AT+UNAME={USER}", 0.4),
        (f"AT+PWD={mqtt_pass}", 0.4),
        (f"AT+CLIENT={CLIENT}", 0.4),
        (f"AT+PUBTOPIC={PUB}", 0.4),
    ]:
        send(cmd, w)
        send(pin, 0.12)

    pre: dict[str, str] = {}
    for key, q in [
        ("PRO", "AT+PRO=?"),
        ("SERVADDR", "AT+SERVADDR=?"),
        ("BKDNS", "AT+BKDNS=?"),
        ("CLIENT", "AT+CLIENT=?"),
        ("PUBTOPIC", "AT+PUBTOPIC=?"),
        ("TDC", "AT+TDC=?"),
    ]:
        for L in send(q, 0.7):
            t = L.strip()
            if t and t != "OK" and not t.startswith("AT+") and "Password" not in t:
                if not t.startswith("[") and "Upload" not in t and "MQTT" not in t:
                    pre[key] = t
                    break
    send("AT+CFG", 3.0)
    log("TEST", f"PRE_ATZ {pre}")

    serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in str(pre.get("SERVADDR", ""))
    if not serv_ok:
        for L in send("AT+SERVADDR=?", 0.8):
            if host_ip in L:
                pre["SERVADDR"] = L.strip()
                serv_ok = True
    if not serv_ok:
        print("=== SUMMARY ===", flush=True)
        print(f"BEFORE={before}", flush=True)
        print(f"PRE_ATZ={pre}", flush=True)
        print("BLOCKER: SERVADDR not confirmed", flush=True)
        print(f"LOG={logpath}", flush=True)
        ser.close()
        return 1

    log("TEST", "ATZ")
    send("ATZ", 1.0)
    print("\n>>> ATZ — wait reboot, PRESS ACT again if needed <<<\n", flush=True)
    read_lines(20.0)

    result2 = unlock(
        ser,
        pin,
        policy="burst",
        timeout=540.0,
        confirm_model="LTC2-CB",
        on_line=lambda L: log("RX", L),
        on_tx=lambda L: log("TX", L),
    )
    unlocked2 = result2.ok
    post: dict[str, str] = {}
    persist_pro = persist_serv = False
    persist_hivemq = False
    if unlocked2:
        log("TEST", "POST unlock — query only")
        for key, q in [
            ("PRO", "AT+PRO=?"),
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("TDC", "AT+TDC=?"),
            ("CSQ", "AT+CSQ"),
        ]:
            for L in send(q, 0.8):
                t = L.strip()
                if t and t != "OK" and not t.startswith("AT+") and not t.startswith("["):
                    if "Password" not in t and "Upload" not in t:
                        post[key] = t
                        break
        for L in send("AT+CFG", 3.5):
            m = re.match(r"AT\+([A-Z0-9]+)=(.*)$", L.strip())
            if m:
                post[m.group(1)] = m.group(2)
        persist_pro = "3,5" in post.get("PRO", "")
        persist_serv = host_ip in post.get("SERVADDR", "") and str(port) in post.get(
            "SERVADDR", ""
        )
        persist_hivemq = "hivemq" in str(post).lower()
        log("TEST", f"POST_ATZ {post}")
        log(
            "TEST",
            f"PERSIST PRO={persist_pro} SERVADDR={persist_serv} HiveMQ={persist_hivemq}",
        )
    else:
        log("TEST", f"post-ATZ unlock fail — {result2.hint}")

    log("TEST", "LISTEN 180s")
    connected = success = False
    fail_send = 0
    markers: list[str] = []
    csq: list[str] = []
    end = time.time() + 180
    while time.time() < end:
        for L in read_lines(1.0):
            if "Successfully connected" in L or "Opened the MQTT" in L:
                connected = True
                markers.append(L[:80])
            if "Upload data successfully" in L:
                success = True
                markers.append(L[:80])
                log("MARK", "UPLINK_SUCCESS")
            if "Failed to send" in L:
                fail_send += 1
                markers.append(L[:80])
                log("MARK", "FAILED_SEND")
            if "Signal Strength" in L or "NBIOT" in L or "NB module" in L:
                csq.append(L.strip()[:100])

    print("=== SUMMARY ===", flush=True)
    print(f"model=LTC2-CB port={COM}", flush=True)
    print(f"BEFORE={before}", flush=True)
    print(f"PRE_ATZ={pre}", flush=True)
    print(f"POST_ATZ={post}", flush=True)
    print(
        f"persist_PRO_3_5={persist_pro} persist_SERVADDR={persist_serv} "
        f"HiveMQ_after_ATZ={persist_hivemq} post_unlock={unlocked2}",
        flush=True,
    )
    print(
        f"mqtt_connected={connected} upload_success={success} "
        f"failed_to_send={fail_send}",
        flush=True,
    )
    print(f"modem_tail={csq[-8:]}", flush=True)
    print(f"markers={markers[-12:]}", flush=True)
    print(f"expected_pub_topic={PUB}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    if unlocked and serv_ok and unlocked2 and persist_pro and persist_serv and not persist_hivemq:
        return 0
    if unlocked and serv_ok:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
