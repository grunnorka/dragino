#!/usr/bin/env python3
"""Race LTC2 BLE config during brief app window (before bootloader reset).

Serial listen-only (no TX). On AT+NAME / SensorManual, connect BLE immediately,
unlock, apply Railway PRO=3,5, then ATZ. After reboot, re-query only (persist test).
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import serial
from bleak import BleakClient, BleakScanner

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
USER = "dragino"
CLIENT = "ltc2"
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
TDC = 120
ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def redacted(s: str, mqtt_pass: str) -> str:
    return s.replace(mqtt_pass, "***").replace(PIN, "***PIN***")


async def amain() -> int:
    load_env(ROOT / "railway-mqtt.local.env")
    mqtt_pass = os.environ["MQTT_PASS"].strip()
    host_ip = os.environ.get("MQTT_FALLBACK_IP", "66.33.22.220").strip()
    port = int(os.environ.get("MQTT_PORT", "33239"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_race_ble_pro35.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s, mqtt_pass)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    app_event = threading.Event()
    stop_serial = threading.Event()
    serial_lines: list[str] = []

    def serial_thread() -> None:
        ser = serial.Serial()
        ser.port = "COM8"
        ser.baudrate = 9600
        ser.timeout = 0.2
        ser.dsrdtr = False
        ser.rtscts = False
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except Exception as exc:  # noqa: BLE001
            log("SYS", f"serial open fail: {exc}")
            return
        ser.dtr = False
        ser.rts = False
        time.sleep(0.15)
        ser.reset_input_buffer()
        log("SYS", "serial listen-only started")
        buf = b""
        while not stop_serial.is_set():
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").rstrip("\r")
                    if not text:
                        continue
                    log("SRX", text)
                    serial_lines.append(text)
                    if (
                        "AT+NAME" in text
                        or "SensorManual" in text
                        or "LTC2 sensor Detected" in text
                    ):
                        app_event.set()
            else:
                time.sleep(0.02)
        ser.close()
        log("SYS", "serial closed")

    th = threading.Thread(target=serial_thread, daemon=True)
    th.start()

    print(
        "\n>>> Press ACT 1-3s now if needed. Racing BLE on next app advertise. <<<\n",
        flush=True,
    )

    inbox: list[str] = []
    unlocked = asyncio.Event()

    def on_notify(_s, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        log("BRX", text.replace("\r", "\\r").replace("\n", "\\n"))
        inbox.append(text)
        if "Password Correct" in text:
            unlocked.set()

    async def send_on(client: BleakClient, cmd: str, wait: float = 1.0) -> None:
        log("BTX", cmd)
        await client.write_gatt_char(
            FFE1, (cmd + "\r\n").encode("ascii"), response=False
        )
        await asyncio.sleep(wait)

    async def unlock_fast(client: BleakClient) -> bool:
        unlocked.clear()
        # Wait briefly for any notify (app console)
        for _ in range(20):
            if inbox:
                break
            await asyncio.sleep(0.1)
        recent = "".join(inbox[-3:])
        if "bootloader" in recent.lower():
            log("SYS", "BLE notify is bootloader — abort unlock")
            return False
        for _ in range(40):
            if unlocked.is_set() or not client.is_connected:
                break
            if any("bootloader" in x.lower() for x in inbox[-3:]):
                log("SYS", "entered bootloader mid-unlock")
                return False
            await send_on(client, PIN, 0.3)
        if unlocked.is_set():
            log("SYS", "BLE unlocked")
            return True
        # sometimes already unlocked
        await send_on(client, "AT+MODEL=?", 1.0)
        if any("LTC2-CB" in x and "SensorManual" not in x for x in inbox[-6:]):
            log("SYS", "BLE already in AT console")
            return True
        log("SYS", "BLE unlock failed")
        return False

    def parse_cfg(blob: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in re.finditer(r"AT\+([A-Z0-9]+)=([^\r\n\\]+)", blob):
            out[m.group(1)] = m.group(2).strip()
        return out

    def summary(cfg: dict[str, str]) -> str:
        return (
            f"PRO={cfg.get('PRO','?')} SERVADDR={cfg.get('SERVADDR','?')} "
            f"BKDNS={cfg.get('BKDNS','?')} CLIENT={cfg.get('CLIENT','?')} "
            f"PUB={cfg.get('PUBTOPIC','?')} TDC={cfg.get('TDC','?')}"
        )

    apply_cmds = [
        ("AT+PRO=3,5", 1.2),
        ("AT+TLSMOD=0,0", 0.8),
        (f"AT+SERVADDR={host_ip},{port}", 1.0),
        (f"AT+BKDNS=1,0,{host_ip},{port}", 1.0),
        (f"AT+CLIENT={CLIENT}", 0.8),
        (f"AT+UNAME={USER}", 0.8),
        (f"AT+PWD={mqtt_pass}", 0.8),
        (f"AT+PUBTOPIC={PUB}", 0.7),
        (f"AT+SUBTOPIC={SUB}", 0.7),
        ("AT+MQOS=1", 0.6),
        (f"AT+TDC={TDC}", 0.8),
        (f"AT+SERVADDR={host_ip},{port}", 0.9),
        (f"AT+BKDNS=1,0,{host_ip},{port}", 0.9),
    ]

    query_cmds = [
        ("AT+PRO=?", 0.9),
        ("AT+SERVADDR=?", 0.9),
        ("AT+BKDNS=?", 0.9),
        ("AT+CLIENT=?", 0.8),
        ("AT+PUBTOPIC=?", 0.8),
        ("AT+TDC=?", 0.8),
        ("AT+CFG", 3.5),
    ]

    # Wait for app window on serial BEFORE BLE connect (avoid bootloader)
    log("SYS", "waiting for SensorManual / AT+NAME on serial...")
    deadline = time.time() + 180
    while time.time() < deadline and not app_event.is_set():
        await asyncio.sleep(0.3)
    if not app_event.is_set():
        log("SYS", "no app window in 180s")
        stop_serial.set()
        th.join(timeout=3)
        return 2

    log("SYS", "app window — BLE scan now")
    # Small settle so GATT is app console not bootloader
    await asyncio.sleep(1.0)
    # Abort if bootloader already showing
    if any("bootloader" in L.lower() for L in serial_lines[-8:]):
        log("SYS", "bootloader already — wait next app window")
        app_event.clear()
        deadline = time.time() + 120
        while time.time() < deadline:
            await asyncio.sleep(0.3)
            if app_event.is_set() and not any(
                "bootloader" in L.lower() for L in serial_lines[-5:]
            ):
                break
        await asyncio.sleep(1.0)

    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=15.0,
    )
    if not device:
        log("SYS", "BLE not found after app window")
        stop_serial.set()
        th.join(timeout=3)
        return 2

    # Reject if serial says bootloader right now
    if any("bootloader" in L.lower() for L in serial_lines[-6:]):
        log("SYS", "bootloader during connect — abort this window")
        stop_serial.set()
        th.join(timeout=3)
        return 4

    log("SYS", f"BLE found {device.address}")
    before: dict[str, str] = {}
    pre: dict[str, str] = {}

    try:
        async with BleakClient(device, timeout=20.0) as client:
            await client.start_notify(FFE1, on_notify)
            if not await unlock_fast(client):
                stop_serial.set()
                th.join(timeout=3)
                return 2

            mark = len(inbox)
            for cmd, w in query_cmds[:6]:
                if not client.is_connected:
                    break
                await send_on(client, cmd, w)
            before = parse_cfg("".join(inbox[mark:]))
            log("SYS", f"BEFORE {summary(before)}")

            for cmd, w in apply_cmds:
                if not client.is_connected:
                    log("SYS", "disconnected mid-apply")
                    break
                await send_on(client, cmd, w)

            mark = len(inbox)
            for cmd, w in query_cmds:
                if not client.is_connected:
                    break
                await send_on(client, cmd, w)
            pre = parse_cfg("".join(inbox[mark:]))
            log("SYS", f"PRE_ATZ {summary(pre)}")

            serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in pre.get(
                "SERVADDR", ""
            )
            if not serv_ok:
                log("SYS", "SERVADDR missing — burst re-apply")
                for cmd, w in apply_cmds:
                    if not client.is_connected:
                        break
                    await send_on(client, cmd, w)
                mark = len(inbox)
                for cmd, w in query_cmds:
                    if not client.is_connected:
                        break
                    await send_on(client, cmd, w)
                pre = parse_cfg("".join(inbox[mark:]))
                log("SYS", f"PRE_ATZ_RETRY {summary(pre)}")
                serv_ok = host_ip in pre.get("SERVADDR", "") and str(port) in pre.get(
                    "SERVADDR", ""
                )

            if not serv_ok:
                print("=== SUMMARY ===", flush=True)
                print(f"BEFORE={summary(before)}", flush=True)
                print(f"PRE_ATZ={summary(pre)}", flush=True)
                print("BLOCKER: SERVADDR not set before ATZ", flush=True)
                print(f"LOG={logpath}", flush=True)
                stop_serial.set()
                th.join(timeout=3)
                return 1

            log("SYS", "ATZ (persist test)")
            try:
                await send_on(client, "ATZ", 1.0)
            except Exception as exc:  # noqa: BLE001
                log("SYS", f"ATZ drop ok: {exc}")
    except Exception as exc:  # noqa: BLE001
        log("SYS", f"BLE session err: {exc}")
        # If we applied before drop, still try post verify
        if not pre:
            stop_serial.set()
            th.join(timeout=3)
            return 3

    print("\n>>> Waiting for next app window (~30s) then BLE re-verify <<<\n", flush=True)
    await asyncio.sleep(12.0)
    app_event.clear()

    # Wait for next advertise
    device2 = None
    deadline = time.time() + 120
    while time.time() < deadline and device2 is None:
        device2 = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name or ad.local_name or "") == NAME,
            timeout=10.0,
        )
    if not device2:
        log("SYS", "post-ATZ BLE not found")
        print("=== SUMMARY ===", flush=True)
        print(f"BEFORE={summary(before)}", flush=True)
        print(f"PRE_ATZ={summary(pre)}", flush=True)
        print("POST_ATZ=no BLE", flush=True)
        print(f"LOG={logpath}", flush=True)
        stop_serial.set()
        th.join(timeout=3)
        return 3

    inbox.clear()
    post: dict[str, str] = {}
    connected = success = False
    fail_send = 0
    markers: list[str] = []

    async with BleakClient(device2, timeout=20.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock_fast(client):
            stop_serial.set()
            th.join(timeout=3)
            return 3

        # query only — no re-apply
        mark = len(inbox)
        for cmd, w in query_cmds:
            if not client.is_connected:
                break
            await send_on(client, cmd, w)
        post = parse_cfg("".join(inbox[mark:]))
        log("SYS", f"POST_ATZ {summary(post)}")

        persist_pro = "3,5" in post.get("PRO", "")
        persist_serv = host_ip in post.get("SERVADDR", "") and str(port) in post.get(
            "SERVADDR", ""
        )
        persist_hivemq = "hivemq" in str(post).lower()
        log(
            "SYS",
            f"PERSIST PRO={persist_pro} SERVADDR={persist_serv} HiveMQ={persist_hivemq}",
        )

        # Listen via serial+ble for uplink chatter until next bootloader or 90s
        log("SYS", "LISTEN 90s")
        end = time.time() + 90
        while time.time() < end:
            await asyncio.sleep(1.0)
            blob = "".join(inbox[-8:] + serial_lines[-20:])
            if "Successfully connected" in blob or "Opened the MQTT" in blob:
                connected = True
            if "Upload data successfully" in blob:
                success = True
                markers.append("Upload data successfully")
            if "Failed to send" in blob:
                fail_send += 1
                markers.append("Failed to send")
            if "bootloader" in blob.lower():
                log("SYS", "bootloader again during listen")
                break

    stop_serial.set()
    th.join(timeout=3)

    print("=== SUMMARY ===", flush=True)
    print("model=LTC2-CB via=BLE+serial_listen", flush=True)
    print(f"BEFORE={summary(before)}", flush=True)
    print(f"PRE_ATZ={summary(pre)}", flush=True)
    print(f"POST_ATZ={summary(post)}", flush=True)
    print(
        f"persist_PRO_3_5={persist_pro} persist_SERVADDR={persist_serv} "
        f"HiveMQ_after_ATZ={persist_hivemq}",
        flush=True,
    )
    print(
        f"mqtt_connected={connected} upload_success={success} failed_to_send={fail_send}",
        flush=True,
    )
    print(f"markers={markers[-12:]}", flush=True)
    print(f"expected_pub_topic={PUB}", flush=True)
    print(f"LOG={logpath}", flush=True)
    # Also dump last serial modem lines
    modemish = [L for L in serial_lines if "NB" in L or "MQTT" in L or "Failed" in L or "Upload" in L]
    print(f"serial_modem_tail={modemish[-8:]}", flush=True)
    return 0 if persist_pro and persist_serv and not persist_hivemq else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
