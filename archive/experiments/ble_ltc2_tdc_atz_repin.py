"""LTC2-CB: force TDC=120 via ATZ, then re-pin Railway MQTT (unique CLIENT)."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
USER = "dragino"
PASS = "DrgN0-MqTt-7kR9wX2pL"
CLIENT = f"ltc2-{NAME}"
HOST_IP = "66.33.22.220"
PORT = 33239
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
TDC = 120
ROOT = Path(__file__).resolve().parent


def redacted(s: str) -> str:
    return s.replace(PASS, "***")


async def amain() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_tdc_atz_repin.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(
        "\n========================================================\n"
        "  LTC2-CB: HOLD ACT 1-3s NOW (not ~12s OTA)\n"
        f"  Target BLE name: {NAME}\n"
        "  Phase1: TDC=120 + ATZ; Phase2: re-pin Railway + verify\n"
        "========================================================\n",
        flush=True,
    )

    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=300.0,
    )
    if not device:
        log("SYS", "not advertising")
        return 1

    inbox: list[str] = []
    unlocked = asyncio.Event()
    saw_rx = asyncio.Event()

    def on_notify(_s, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        log("RX", text.replace("\r", "\\r").replace("\n", "\\n"))
        inbox.append(text)
        saw_rx.set()
        if "Password Correct" in text or "LTC2-CB" in text:
            unlocked.set()

    async def unlock(client: BleakClient, label: str) -> bool:
        async def send(cmd: str, wait: float = 1.2) -> None:
            log("TX", cmd)
            await client.write_gatt_char(
                FFE1, (cmd + "\r\n").encode("ascii"), response=False
            )
            await asyncio.sleep(wait)

        # Wait for console chatter (echo off / modem) before PIN
        log("SYS", f"{label}: wait console ready (max 18s)")
        try:
            await asyncio.wait_for(saw_rx.wait(), timeout=18.0)
            await asyncio.sleep(1.5)
        except asyncio.TimeoutError:
            log("SYS", f"{label}: no spontaneous RX; try unlock anyway")
            await asyncio.sleep(8.0)

        await send("AT+MODEL=?", 1.2)
        if not unlocked.is_set():
            for _ in range(20):
                if unlocked.is_set() or not client.is_connected:
                    break
                await send(PIN, 0.35)
        if unlocked.is_set():
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

    # ---- Phase 1: set TDC then ATZ ----
    async with BleakClient(device, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock(client, "phase1"):
            return 2

        for cmd, wait in [
            (f"AT+TDC={TDC}", 1.5),
            (f"AT+TDC={TDC}", 1.3),
            ("AT+TDC=?", 1.3),
        ]:
            await send_on(client, cmd, wait)

        log("SYS", "ATZ for TDC persistence")
        try:
            await send_on(client, "ATZ", 1.5)
        except Exception as exc:  # noqa: BLE001
            log("SYS", f"ATZ drop (expected): {exc}")

    print(
        "\n========================================================\n"
        "  REBOOTING — wait ~12s, then HOLD ACT 1-3s AGAIN\n"
        "  for Railway re-pin + CFG verify\n"
        "========================================================\n",
        flush=True,
    )
    await asyncio.sleep(12.0)

    # ---- Phase 2: reconnect, re-pin Railway, verify ----
    inbox.clear()
    unlocked.clear()
    saw_rx.clear()

    device2 = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=300.0,
    )
    if not device2:
        log("SYS", "phase2: not advertising")
        return 3

    async with BleakClient(device2, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock(client, "phase2"):
            return 2

        repair = [
            ("AT+PRO=3,5", 2.2),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 1.6),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 1.6),
            (f"AT+UNAME={USER}", 1.4),
            (f"AT+PWD={PASS}", 1.4),
            (f"AT+CLIENT={CLIENT}", 1.4),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+SUBTOPIC={SUB}", 1.3),
            ("AT+MQOS=1", 1.2),
            (f"AT+TDC={TDC}", 1.4),
            # re-assert after PRO (HiveMQ rewrite risk)
            (f"AT+SERVADDR={HOST_IP},{PORT}", 1.5),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 1.5),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={PASS}", 1.3),
            (f"AT+CLIENT={CLIENT}", 1.3),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+TDC={TDC}", 1.3),
            ("AT+CFG", 5.0),
            ("AT+SERVADDR=?", 1.3),
            ("AT+BKDNS=?", 1.3),
            ("AT+PRO=?", 1.3),
            ("AT+TDC=?", 1.3),
            ("AT+UNAME=?", 1.3),
            ("AT+CLIENT=?", 1.3),
            ("AT+PUBTOPIC=?", 1.3),
            ("AT+SUBTOPIC=?", 1.3),
            ("AT+TLSMOD=?", 1.3),
            ("AT+LDATA=?", 1.5),
        ]
        for cmd, wait in repair:
            if not client.is_connected:
                log("SYS", "disconnected during repair")
                return 4
            await send_on(client, cmd, wait)

        allrx = "".join(inbox)
        checks = {
            "PRO_3_5": "AT+PRO=3,5" in allrx,
            "SERVADDR": HOST_IP in allrx and "broker.hivemq.com" not in allrx.lower(),
            "BKDNS": f"1,0,{HOST_IP},{PORT}" in allrx or HOST_IP in allrx,
            "UNAME": f"AT+UNAME={USER}" in allrx or f"\r\n{USER}\r\n" in allrx,
            "CLIENT": CLIENT in allrx and "AT+CLIENT=null" not in allrx,
            "PUB": PUB in allrx,
            "SUB": SUB in allrx,
            "TDC_120": f"AT+TDC={TDC}" in allrx or f"\r\n{TDC}\r\n" in allrx,
            "TLS_off": "AT+TLSMOD=0,0" in allrx,
            "no_HiveMQ": "broker.hivemq.com" not in allrx.lower(),
            "no_TB_pub": "v1/devices/me/telemetry" not in allrx,
        }
        if "AT+TDC=1800" in allrx and f"AT+TDC={TDC}" not in allrx:
            checks["TDC_120"] = False
        if "AT+CLIENT=null" in allrx:
            checks["CLIENT"] = False

        log("SYS", "--- verify ---")
        ok = True
        for k, v in checks.items():
            log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
            ok = ok and v
        uploadish = any(
            x in allrx.lower()
            for x in ("upload start", "mqtt", "connect", "publish", "auth")
        )
        log("SYS", f"upload_chatter_seen={uploadish}")
        log("SYS", f"done ok={ok} log={logpath}")
        return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
