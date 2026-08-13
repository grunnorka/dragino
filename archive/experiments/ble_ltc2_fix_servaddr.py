"""Fix LTC2-CB SERVADDR HiveMQ jump → ThingsBoard IP over BLE."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
TOKEN = "cdHsbYNjHJ7haAPkoJZD"
SERVADDR = "167.235.104.181,1883"
ROOT = Path(__file__).resolve().parent


async def amain() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_fix_servaddr.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {s}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(">>> Hold ACT 1-3s NOW <<<", flush=True)
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=90.0,
    )
    if not device:
        log("SYS", "not advertising")
        return 1

    inbox: list[str] = []
    unlocked = asyncio.Event()

    def on_notify(_s, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        log("RX", text.replace("\r", "\\r").replace("\n", "\\n"))
        inbox.append(text)
        if "Password Correct" in text or "LTC2-CB" in text:
            unlocked.set()

    async with BleakClient(device, timeout=30.0) as client:
        await client.start_notify(FFE1, on_notify)

        async def send(cmd: str, wait: float = 1.3) -> str:
            log("TX", cmd)
            before = len(inbox)
            try:
                await client.write_gatt_char(
                    FFE1, (cmd + "\r\n").encode("ascii"), response=True
                )
            except Exception:
                await client.write_gatt_char(
                    FFE1, (cmd + "\r\n").encode("ascii"), response=False
                )
            await asyncio.sleep(wait)
            return "".join(inbox[before:])

        log("SYS", "wait 12s settle...")
        await asyncio.sleep(12.0)

        await send("AT+MODEL=?", 1.2)
        if not unlocked.is_set():
            for _ in range(10):
                if unlocked.is_set():
                    break
                await send(PIN, 0.35)
        if not unlocked.is_set():
            log("SYS", "unlock failed")
            return 2
        log("SYS", "unlocked")

        # Re-assert ThingsBoard stack (SERVADDR AFTER any PRO touch)
        for cmd, wait in [
            ("AT+PRO=3,3", 2.0),
            (f"AT+SERVADDR={SERVADDR}", 1.5),
            (f"AT+UNAME={TOKEN}", 1.4),
            ("AT+PWD=NULL", 1.2),
            ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.3),
            ("AT+SUBTOPIC=v1/devices/me/attributes", 1.3),
            ("AT+CLIENT=null", 1.2),
            ("AT+MQOS=1", 1.2),
            ("AT+TLSMOD=0,0", 1.2),
            ("AT+BKDNS=1,0,167.235.104.181,1883", 1.5),
            (f"AT+SERVADDR={SERVADDR}", 1.5),  # again after PRO
            ("AT+CFG", 4.5),
            ("AT+SERVADDR=?", 1.4),
            ("AT+BKDNS=?", 1.4),
        ]:
            if not client.is_connected:
                break
            await send(cmd, wait)

        allrx = "".join(inbox)
        hivemq = "broker.hivemq.com" in allrx.lower()
        # CFG line must be IP, not HiveMQ
        cfg_ok = f"AT+SERVADDR={SERVADDR}" in allrx
        query_ok = "167.235.104.181,1883" in allrx
        log("SYS", "--- result ---")
        log("SYS", f"{'PASS' if cfg_ok else 'FAIL'}: CFG SERVADDR IP")
        log("SYS", f"{'FAIL' if hivemq and not cfg_ok else 'OK  '}: HiveMQ present={hivemq}")
        log("SYS", f"{'PASS' if query_ok else 'FAIL'}: IP seen in session")
        log("SYS", f"done log={logpath}")
        # Success if CFG shows IP address as primary SERVADDR
        return 0 if cfg_ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
