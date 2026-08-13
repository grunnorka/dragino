#!/usr/bin/env python3
"""LTC2-CB: set PRO=3,5 + Railway SERVADDR, ATZ persistence check, uplink listen.

Uses main Railway proxy IP (66.33.22.220:33239), NOT HiveMQ.
Does NOT re-apply after ATZ — that is the HiveMQ regression test.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
PIN = "358613"
COM = "COM8"
BAUD = 9600
USER = "dragino"
CLIENT = "ltc2"
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
TDC = 120


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    # Prefer main Railway TCP proxy (altaria) IP form for DNS-flaky LTE
    host_ip = os.environ.get("MQTT_FALLBACK_IP", "66.33.22.220").strip()
    port = int(os.environ.get("MQTT_PORT", "33239"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_pro35_persist.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    ser = serial.Serial(COM, BAUD, timeout=0.2, write_timeout=2)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.15)
    print(f"Opened {COM}@{BAUD}; log={logpath}", flush=True)
    print(f"Target SERVADDR={host_ip},{port}  PRO=3,5  topic={PUB}", flush=True)
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

    def send(cmd: str, wait: float = 1.4) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def wake_unlock(timeout: float = 180.0, label: str = "unlock") -> bool:
        """Wait for console wake cues, then PIN — require 'Password Correct'.

        Do NOT treat boot banner 'LTC2-CB SensorManual' as unlock (false positive).
        """
        print(f"\n>>> HOLD ACT 1-3s on LTC2 NOW ({label}) <<<\n", flush=True)
        log("TEST", f"WAKE_UNLOCK {label} {timeout}s")
        deadline = time.time() + timeout
        in_up = False
        last_pin = 0.0
        while time.time() < deadline:
            lines = read_lines(0.8)
            if any("Password Correct" in L for L in lines):
                m = send("AT+MODEL=?", 1.4)
                if any(re.match(r"^LTC2-CB", x.strip()) for x in m):
                    log("TEST", f"UNLOCK_OK {label} (confirmed MODEL)")
                    return True
                log("TEST", f"UNLOCK_OK {label} (Password Correct)")
                return True
            if any("Upload start" in L for L in lines):
                in_up = True
            if any(("End of upload" in L) or ("power-off" in L.lower()) for L in lines):
                in_up = False
            if in_up:
                continue
            wake = any(
                L.strip() == "RDY"
                or "Signal Strength" in L
                or "Echo mode" in L
                or "NBIOT has responded" in L
                or "Please" in L
                or "password" in L.lower()
                or "Password" in L
                for L in lines
            )
            now = time.time()
            # Gentle PIN probes (no AT+MODEL spam — that can hit bootloader)
            if wake or (now - last_pin >= 4.0):
                last_pin = now
                r = send(PIN, 1.6)
                if any("Password Correct" in x for x in r) or any(
                    "Password Correct" in x for x in read_lines(0.6)
                ):
                    m = send("AT+MODEL=?", 1.4)
                    if any(re.match(r"^LTC2-CB", x.strip()) for x in m):
                        log("TEST", f"UNLOCK_OK {label} (confirmed MODEL)")
                    else:
                        log("TEST", f"UNLOCK_OK {label}")
                    return True
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
            for L in send(cmd, 1.6):
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
            ("AT+TLSMOD=0,0", 1.4),
            (f"AT+SERVADDR={host_ip},{port}", 1.8),
            (f"AT+BKDNS=1,0,{host_ip},{port}", 1.8),
            (f"AT+CLIENT={CLIENT}", 1.4),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={mqtt_pass}", 1.3),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+SUBTOPIC={SUB}", 1.3),
            ("AT+MQOS=1", 1.2),
            (f"AT+TDC={TDC}", 1.4),
            # re-assert after PRO (HiveMQ rewrite risk)
            (f"AT+SERVADDR={host_ip},{port}", 1.6),
            (f"AT+BKDNS=1,0,{host_ip},{port}", 1.6),
            (f"AT+CLIENT={CLIENT}", 1.3),
            (f"AT+UNAME={USER}", 1.2),
            (f"AT+PWD={mqtt_pass}", 1.2),
            (f"AT+PUBTOPIC={PUB}", 1.2),
            (f"AT+SUBTOPIC={SUB}", 1.2),
            (f"AT+TDC={TDC}", 1.2),
        ]:
            send(cmd, w)

    def summary_bits(cfg: dict[str, str]) -> str:
        return (
            f"PRO={cfg.get('PRO','?')} SERVADDR={cfg.get('SERVADDR','?')} "
            f"BKDNS={cfg.get('BKDNS','?')} CLIENT={cfg.get('CLIENT','?')} "
            f"PUB={cfg.get('PUBTOPIC','?')} TDC={cfg.get('TDC','?')} "
            f"CSQ={cfg.get('CSQ','?')}"
        )

    if not wake_unlock(180.0, "pre-config"):
        log("TEST", "UNLOCK_FAIL")
        ser.close()
        print("BLOCKED: press ACT 1-3s on LTC2 and re-run.", flush=True)
        return 2

    log("TEST", "BEFORE snapshot")
    before = query_map()
    log("TEST", f"BEFORE {summary_bits(before)}")

    apply()
    pre = query_map()
    log("TEST", f"PRE_ATZ {summary_bits(pre)}")
    serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in pre.get("SERVADDR", "")
    pro_ok = "3,5" in pre.get("PRO", "")
    hivemq = "hivemq" in str(pre).lower()
    if not serv_ok or not pro_ok or hivemq:
        log("TEST", "PRE_ATZ verify failed — re-apply once")
        apply()
        pre = query_map()
        log("TEST", f"PRE_ATZ_RETRY {summary_bits(pre)}")
        serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in pre.get("SERVADDR", "")
        pro_ok = "3,5" in pre.get("PRO", "")
        hivemq = "hivemq" in str(pre).lower()

    if not serv_ok:
        print("=== SUMMARY ===", flush=True)
        print(f"BEFORE={summary_bits(before)}", flush=True)
        print(f"PRE_ATZ={summary_bits(pre)}", flush=True)
        print("BLOCKER: SERVADDR not Railway; skipped ATZ", flush=True)
        print(f"LOG={logpath}", flush=True)
        ser.close()
        return 1

    log("TEST", "ATZ (persistence test — no re-apply after)")
    send("ATZ", 1.5)
    print("\n>>> REBOOT — HOLD ACT 1-3s in ~15s <<<\n", flush=True)
    read_lines(15.0)

    if not wake_unlock(180.0, "post-ATZ"):
        log("TEST", "post-ATZ unlock fail")
        ser.close()
        return 3

    # CRITICAL: query only — do not re-apply
    post = query_map()
    log("TEST", f"POST_ATZ {summary_bits(post)}")
    persist_pro = "3,5" in post.get("PRO", "")
    persist_serv = host_ip in post.get("SERVADDR", "") and str(port) in post.get("SERVADDR", "")
    persist_hivemq = "hivemq" in str(post).lower()
    log("TEST", f"PERSIST PRO={persist_pro} SERVADDR={persist_serv} HiveMQ={persist_hivemq}")

    log("TEST", "LISTEN uplink ~240s")
    send("AT+CSQ", 1.5)
    deadline = time.time() + 240
    connected = success = False
    fail_send = 0
    csq_seen: list[str] = []
    markers: list[str] = []
    while time.time() < deadline:
        for L in read_lines(1.0):
            low = L.lower()
            if "Signal Strength" in L or re.match(r"^\+CSQ:", L) or re.match(r"^\d+,\d+$", L.strip()):
                csq_seen.append(L.strip())
            if "Opened the MQTT" in L or "Successfully connected to the server" in L:
                connected = True
                markers.append(L)
            if "Upload data successfully" in L or "send data ok" in low:
                success = True
                markers.append(L)
                log("MARK", "UPLINK_SUCCESS")
            if "Failed to send" in L:
                fail_send += 1
                markers.append(L)
                log("MARK", "FAILED_SEND")
            if "not authorised" in low or "not authorized" in low:
                markers.append(L)
                log("MARK", "AUTH_FAIL")

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
        f"failed_to_send={fail_send} csq={csq_seen[-5:]}",
        flush=True,
    )
    print(f"markers={markers[-12:]}", flush=True)
    print(f"expected_pub_topic={PUB}", flush=True)
    print(f"LOG={logpath}", flush=True)
    ser.close()
    return 0 if persist_pro and persist_serv and not persist_hivemq else 1


if __name__ == "__main__":
    raise SystemExit(main())
