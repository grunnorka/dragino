#!/usr/bin/env python3
"""Quiet UART unlock + PRO=3,5 Railway apply + ATZ persistence (no re-apply).

Uses shared/dragino_uart quiet policy (no PIN during bootloader/upload).
"""
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
    logpath = ROOT / "logs" / f"{stamp}_ltc2_quiet_pro35_persist.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = open_serial(COM, BAUD)
    buf = LineBuffer()
    print(f"Opened {COM} DTR/RTS low; log={logpath}", flush=True)
    print(f"Target SERVADDR={host_ip},{port} PRO=3,5 topic={PUB}", flush=True)
    print(
        "\n>>> Ensure SW1=Flash (NOT ISP). HOLD ACT 1-3s when ready. <<<\n",
        flush=True,
    )

    def log(tag: str, s: str) -> None:
        safe = s.replace(mqtt_pass, "***").replace(pin, "***PIN***")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(seconds: float) -> list[str]:
        return read_for(ser, seconds, buf, lambda L: log("RX", L))

    def send(cmd: str, wait: float = 1.2) -> list[str]:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        log("TX", shown)
        send_line(ser, cmd)
        return read_lines(wait)

    def wake_unlock(timeout: float = 240.0, label: str = "unlock") -> bool:
        log("TEST", f"QUIET_UNLOCK {label} {timeout}s")
        result = unlock(
            ser,
            pin,
            policy="quiet",
            timeout=timeout,
            confirm_model="LTC2-CB",
            on_line=lambda L: log("RX", L),
            on_tx=lambda L: log("TX", L),
        )
        if result.ok:
            log("TEST", f"UNLOCK_OK {label}")
            return True
        log("TEST", f"UNLOCK_FAIL {label} phase={result.phase.value} {result.hint}")
        print(f"BLOCKED: {result.hint}", flush=True)
        return False

    def query_map() -> dict[str, str]:
        out: dict[str, str] = {}
        for key, cmd in [
            ("MODEL", "AT+MODEL=?"),
            ("SERVADDR", "AT+SERVADDR=?"),
            ("BKDNS", "AT+BKDNS=?"),
            ("PRO", "AT+PRO=?"),
            ("CLIENT", "AT+CLIENT=?"),
            ("UNAME", "AT+UNAME=?"),
            ("PUBTOPIC", "AT+PUBTOPIC=?"),
            ("SUBTOPIC", "AT+SUBTOPIC=?"),
            ("TLSMOD", "AT+TLSMOD=?"),
            ("TDC", "AT+TDC=?"),
            ("CSQ", "AT+CSQ"),
        ]:
            for L in send(cmd, 1.5):
                t = L.strip()
                if (
                    not t
                    or t == "OK"
                    or t.startswith("[")
                    or t.startswith("AT+")
                    or "Password" in t
                    or "Attention" in t
                    or "Upload" in t
                    or "MQTT" in t
                    or "Failed" in t
                    or "Domain" in t
                ):
                    continue
                out[key] = t
                break
        for L in send("AT+CFG", 6.0):
            m = re.match(r"AT\+([A-Z0-9]+)=(.*)$", L.strip())
            if m:
                out[m.group(1)] = m.group(2)
        return out

    def apply() -> None:
        log("TEST", "APPLY Railway PRO=3,5")
        for cmd, w in [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={host_ip},{port}", 1.6),
            (f"AT+BKDNS=1,0,{host_ip},{port}", 1.6),
            (f"AT+CLIENT={CLIENT}", 1.3),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={mqtt_pass}", 1.3),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
            ("AT+MQOS=1", 1.1),
            (f"AT+TDC={TDC}", 1.3),
            (f"AT+SERVADDR={host_ip},{port}", 1.5),
            (f"AT+BKDNS=1,0,{host_ip},{port}", 1.5),
            (f"AT+CLIENT={CLIENT}", 1.2),
            (f"AT+UNAME={USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+TDC={TDC}", 1.2),
        ]:
            send(cmd, w)

    def summary_bits(cfgm: dict[str, str]) -> str:
        return (
            f"PRO={cfgm.get('PRO','?')} SERVADDR={cfgm.get('SERVADDR','?')} "
            f"BKDNS={cfgm.get('BKDNS','?')} CLIENT={cfgm.get('CLIENT','?')} "
            f"PUB={cfgm.get('PUBTOPIC','?')} TDC={cfgm.get('TDC','?')} "
            f"CSQ={cfgm.get('CSQ','?')}"
        )

    if not wake_unlock(240.0, "pre-config"):
        ser.close()
        return 2

    before = query_map()
    log("TEST", f"BEFORE {summary_bits(before)}")
    apply()
    pre = query_map()
    log("TEST", f"PRE_ATZ {summary_bits(pre)}")
    serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in pre.get("SERVADDR", "")
    if not serv_ok:
        apply()
        pre = query_map()
        log("TEST", f"PRE_ATZ_RETRY {summary_bits(pre)}")
        serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in pre.get(
            "SERVADDR", ""
        )
    if not serv_ok:
        print("=== SUMMARY ===", flush=True)
        print(f"BEFORE={summary_bits(before)}", flush=True)
        print(f"PRE_ATZ={summary_bits(pre)}", flush=True)
        print("BLOCKER: SERVADDR not Railway", flush=True)
        print(f"LOG={logpath}", flush=True)
        ser.close()
        return 1

    log("TEST", "ATZ persistence (no re-apply)")
    send("ATZ", 1.5)
    print("\n>>> REBOOT — HOLD ACT 1-3s in ~15s <<<\n", flush=True)
    read_lines(15.0)

    if not wake_unlock(240.0, "post-ATZ"):
        ser.close()
        return 3

    post = query_map()
    log("TEST", f"POST_ATZ {summary_bits(post)}")
    persist_pro = "3,5" in post.get("PRO", "")
    persist_serv = host_ip in post.get("SERVADDR", "") and str(port) in post.get(
        "SERVADDR", ""
    )
    persist_hivemq = "hivemq" in str(post).lower()
    log(
        "TEST",
        f"PERSIST PRO={persist_pro} SERVADDR={persist_serv} HiveMQ={persist_hivemq}",
    )

    log("TEST", "LISTEN 240s")
    connected = success = False
    fail_send = 0
    markers: list[str] = []
    csq: list[str] = []
    end = time.time() + 240
    while time.time() < end:
        for L in read_lines(1.0):
            if "Successfully connected" in L or "Opened the MQTT" in L:
                connected = True
                markers.append(L)
            if "Upload data successfully" in L:
                success = True
                markers.append(L)
                log("MARK", "UPLINK_SUCCESS")
            if "Failed to send" in L:
                fail_send += 1
                markers.append(L)
                log("MARK", "FAILED_SEND")
            if "Signal Strength" in L or re.match(r"^\+CSQ:", L):
                csq.append(L.strip())

    print("=== SUMMARY ===", flush=True)
    print(f"model=LTC2-CB port={COM}", flush=True)
    print(f"BEFORE={summary_bits(before)}", flush=True)
    print(f"PRE_ATZ={summary_bits(pre)}", flush=True)
    print(f"POST_ATZ={summary_bits(post)}", flush=True)
    print(
        f"persist_PRO_3_5={persist_pro} persist_SERVADDR={persist_serv} "
        f"HiveMQ_after_ATZ={persist_hivemq}",
        flush=True,
    )
    print(
        f"mqtt_connected={connected} upload_success={success} "
        f"failed_to_send={fail_send} csq={csq[-5:]}",
        flush=True,
    )
    print(f"markers={markers[-12:]}", flush=True)
    print(f"expected_pub_topic={PUB}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    return 0 if persist_pro and persist_serv and not persist_hivemq else 1


if __name__ == "__main__":
    raise SystemExit(main())
