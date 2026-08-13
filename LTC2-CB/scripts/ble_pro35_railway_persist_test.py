#!/usr/bin/env python3
"""LTC2-CB BLE: PRO=3,5 + Railway SERVADDR, ATZ persistence (no re-apply), uplink listen."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
USER = "dragino"
CLIENT = "ltc2"
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
TDC = 120
HOST_IP = "66.33.22.220"
PORT = 33239
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
    host_ip = os.environ.get("MQTT_FALLBACK_IP", HOST_IP).strip()
    port = int(os.environ.get("MQTT_PORT", str(PORT)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_ble_pro35_persist.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s, mqtt_pass)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(
        "\n========================================================\n"
        "  LTC2 BLE PRO=3,5 Railway persist test\n"
        f"  SERVADDR={host_ip},{port}  topic={PUB}\n"
        "  HOLD ACT 1-3s if not advertising\n"
        "========================================================\n",
        flush=True,
    )

    inbox: list[str] = []
    unlocked = asyncio.Event()
    saw_rx = asyncio.Event()

    def on_notify(_s, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        log("RX", text.replace("\r", "\\r").replace("\n", "\\n"))
        inbox.append(text)
        saw_rx.set()
        if "Password Correct" in text:
            unlocked.set()

    async def find_dev(timeout: float = 120.0):
        return await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name or ad.local_name or "") == NAME,
            timeout=timeout,
        )

    async def unlock(client: BleakClient, label: str) -> bool:
        unlocked.clear()
        saw_rx.clear()

        async def send(cmd: str, wait: float = 1.0) -> None:
            log("TX", cmd)
            await client.write_gatt_char(
                FFE1, (cmd + "\r\n").encode("ascii"), response=False
            )
            await asyncio.sleep(wait)

        log("SYS", f"{label}: wait RX / unlock")
        try:
            await asyncio.wait_for(saw_rx.wait(), timeout=20.0)
            await asyncio.sleep(1.0)
        except asyncio.TimeoutError:
            log("SYS", f"{label}: no spontaneous RX; settle then PIN")
            await asyncio.sleep(3.0)

        for _ in range(25):
            if unlocked.is_set() or not client.is_connected:
                break
            await send(PIN, 0.4)
        if unlocked.is_set():
            await send("AT+MODEL=?", 1.2)
            log("SYS", f"{label}: unlocked")
            return True
        log("SYS", f"{label}: unlock failed")
        return False

    async def send_on(client: BleakClient, cmd: str, wait: float = 1.3) -> None:
        log("TX", cmd)
        await client.write_gatt_char(
            FFE1, (cmd + "\r\n").encode("ascii"), response=False
        )
        await asyncio.sleep(wait)

    async def wait_upload_idle(max_s: float = 60.0) -> None:
        deadline = asyncio.get_event_loop().time() + max_s
        while asyncio.get_event_loop().time() < deadline:
            recent = "".join(inbox[-8:])
            if "Upload start" in recent and "End of upload" not in recent:
                log("SYS", "upload in progress — waiting")
                await asyncio.sleep(2.5)
                continue
            return

    def parse_cfg(blob: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in re.finditer(r"AT\+([A-Z0-9]+)=([^\r\n]+)", blob):
            out[m.group(1)] = m.group(2).strip()
        # query replies often bare
        for key, pat in [
            ("PRO", r"(?:AT\+PRO=\?[\s\S]*?)(\d,\d)"),
            ("SERVADDR", r"(?:AT\+SERVADDR=\?[\s\S]*?)([0-9a-zA-Z\.\-]+,\d+)"),
            ("TDC", r"(?:AT\+TDC=\?[\s\S]*?)(\d+)"),
        ]:
            if key not in out:
                m = re.search(pat, blob)
                if m:
                    out[key] = m.group(1)
        return out

    def summary(cfg: dict[str, str]) -> str:
        return (
            f"PRO={cfg.get('PRO','?')} SERVADDR={cfg.get('SERVADDR','?')} "
            f"BKDNS={cfg.get('BKDNS','?')} CLIENT={cfg.get('CLIENT','?')} "
            f"PUB={cfg.get('PUBTOPIC','?')} TDC={cfg.get('TDC','?')}"
        )

    apply_cmds = [
        ("AT+PRO=3,5", 2.2),
        ("AT+TLSMOD=0,0", 1.3),
        (f"AT+SERVADDR={host_ip},{port}", 1.6),
        (f"AT+BKDNS=1,0,{host_ip},{port}", 1.6),
        (f"AT+UNAME={USER}", 1.4),
        (f"AT+PWD={mqtt_pass}", 1.4),
        (f"AT+CLIENT={CLIENT}", 1.4),
        (f"AT+PUBTOPIC={PUB}", 1.3),
        (f"AT+SUBTOPIC={SUB}", 1.3),
        ("AT+MQOS=1", 1.2),
        (f"AT+TDC={TDC}", 1.4),
        (f"AT+SERVADDR={host_ip},{port}", 1.5),
        (f"AT+BKDNS=1,0,{host_ip},{port}", 1.5),
        (f"AT+UNAME={USER}", 1.3),
        (f"AT+PWD={mqtt_pass}", 1.3),
        (f"AT+CLIENT={CLIENT}", 1.3),
        (f"AT+PUBTOPIC={PUB}", 1.3),
        (f"AT+TDC={TDC}", 1.3),
    ]

    query_cmds = [
        ("AT+MODEL=?", 1.3),
        ("AT+PRO=?", 1.3),
        ("AT+SERVADDR=?", 1.3),
        ("AT+BKDNS=?", 1.3),
        ("AT+UNAME=?", 1.3),
        ("AT+CLIENT=?", 1.3),
        ("AT+PUBTOPIC=?", 1.3),
        ("AT+SUBTOPIC=?", 1.3),
        ("AT+TDC=?", 1.3),
        ("AT+TLSMOD=?", 1.3),
        ("AT+CSQ", 1.5),
        ("AT+CFG", 5.0),
    ]

    device = await find_dev(120.0)
    if not device:
        log("SYS", "not advertising — press ACT 1-3s")
        return 2

    before_cfg: dict[str, str] = {}
    pre_cfg: dict[str, str] = {}

    async with BleakClient(device, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock(client, "phase1"):
            return 2
        await wait_upload_idle(45.0)

        mark = len(inbox)
        for cmd, w in query_cmds:
            await send_on(client, cmd, w)
        before_cfg = parse_cfg("".join(inbox[mark:]))
        log("SYS", f"BEFORE {summary(before_cfg)}")

        await wait_upload_idle(30.0)
        for cmd, w in apply_cmds:
            if not client.is_connected:
                log("SYS", "disconnected during apply")
                break
            await send_on(client, cmd, w)

        mark = len(inbox)
        for cmd, w in query_cmds:
            if not client.is_connected:
                break
            await send_on(client, cmd, w)
        pre_cfg = parse_cfg("".join(inbox[mark:]))
        log("SYS", f"PRE_ATZ {summary(pre_cfg)}")

        serv_ok = host_ip in pre_cfg.get("SERVADDR", "") and str(port) in pre_cfg.get(
            "SERVADDR", ""
        )
        pro_ok = "3,5" in pre_cfg.get("PRO", "")
        if not serv_ok or not pro_ok:
            log("SYS", "PRE_ATZ fail — re-apply once")
            for cmd, w in apply_cmds:
                if not client.is_connected:
                    break
                await send_on(client, cmd, w)
            mark = len(inbox)
            for cmd, w in query_cmds:
                if not client.is_connected:
                    break
                await send_on(client, cmd, w)
            pre_cfg = parse_cfg("".join(inbox[mark:]))
            log("SYS", f"PRE_ATZ_RETRY {summary(pre_cfg)}")
            serv_ok = host_ip in pre_cfg.get("SERVADDR", "") and str(port) in pre_cfg.get(
                "SERVADDR", ""
            )

        if not serv_ok:
            print("=== SUMMARY ===", flush=True)
            print(f"BEFORE={summary(before_cfg)}", flush=True)
            print(f"PRE_ATZ={summary(pre_cfg)}", flush=True)
            print("BLOCKER: SERVADDR not Railway; skipped ATZ", flush=True)
            print(f"LOG={logpath}", flush=True)
            return 1

        log("SYS", "ATZ — persistence test (NO re-apply after)")
        try:
            await send_on(client, "ATZ", 1.5)
        except Exception as exc:  # noqa: BLE001
            log("SYS", f"ATZ drop (expected): {exc}")

    print(
        "\n>>> REBOOT — wait ~15s, HOLD ACT 1-3s for BLE advertise <<<\n",
        flush=True,
    )
    await asyncio.sleep(15.0)

    device2 = await find_dev(180.0)
    if not device2:
        log("SYS", "post-ATZ not advertising")
        print("=== SUMMARY ===", flush=True)
        print(f"BEFORE={summary(before_cfg)}", flush=True)
        print(f"PRE_ATZ={summary(pre_cfg)}", flush=True)
        print("POST_ATZ=unlock_fail (no BLE)", flush=True)
        print(f"LOG={logpath}", flush=True)
        return 3

    inbox.clear()
    post_cfg: dict[str, str] = {}
    markers: list[str] = []
    connected = success = False
    fail_send = 0

    async with BleakClient(device2, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock(client, "post-ATZ"):
            return 3
        await wait_upload_idle(45.0)

        # CRITICAL: query only
        mark = len(inbox)
        for cmd, w in query_cmds:
            if not client.is_connected:
                break
            await send_on(client, cmd, w)
        post_cfg = parse_cfg("".join(inbox[mark:]))
        log("SYS", f"POST_ATZ {summary(post_cfg)}")

        persist_pro = "3,5" in post_cfg.get("PRO", "")
        persist_serv = host_ip in post_cfg.get("SERVADDR", "") and str(port) in post_cfg.get(
            "SERVADDR", ""
        )
        persist_hivemq = "hivemq" in str(post_cfg).lower()
        log(
            "SYS",
            f"PERSIST PRO={persist_pro} SERVADDR={persist_serv} HiveMQ={persist_hivemq}",
        )

        log("SYS", "LISTEN uplink ~180s via BLE notify")
        deadline = asyncio.get_event_loop().time() + 180
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
            recent = "".join(inbox[-6:])
            if "Successfully connected" in recent or "Opened the MQTT" in recent:
                connected = True
            if "Upload data successfully" in recent:
                success = True
                markers.append("Upload data successfully")
                log("MARK", "UPLINK_SUCCESS")
            if "Failed to send" in recent:
                fail_send += 1
                markers.append("Failed to send")
                log("MARK", "FAILED_SEND")

    print("=== SUMMARY ===", flush=True)
    print("model=LTC2-CB via=BLE name=869181074162403", flush=True)
    print(f"BEFORE={summary(before_cfg)}", flush=True)
    print(f"PRE_ATZ={summary(pre_cfg)}", flush=True)
    print(f"POST_ATZ={summary(post_cfg)}", flush=True)
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
    return 0 if persist_pro and persist_serv and not persist_hivemq else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
