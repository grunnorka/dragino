"""Fix LTC2 PUBTOPIC / TDC / BKDNS after Railway auth."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
PASS = "DrgN0-MqTt-7kR9wX2pL"
ROOT = Path(__file__).resolve().parent


def redacted(s: str) -> str:
    return s.replace(PASS, "***")


async def amain() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_pub_tdc.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(">>> ACT 1-3s NOW — fix PUBTOPIC+TDC+BKDNS <<<", flush=True)
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

        async def send(cmd: str, wait: float = 1.3) -> None:
            log("TX", cmd)
            await client.write_gatt_char(
                FFE1, (cmd + "\r\n").encode("ascii"), response=False
            )
            await asyncio.sleep(wait)

        await asyncio.sleep(8.0)
        await send("AT+MODEL=?", 1.0)
        if not unlocked.is_set():
            for _ in range(12):
                if unlocked.is_set():
                    break
                await send(PIN, 0.3)
        if not unlocked.is_set():
            log("SYS", "unlock fail")
            return 2

        for cmd, wait in [
            ("AT+PUBTOPIC=dragino/ltc2/up", 1.4),
            ("AT+SUBTOPIC=dragino/ltc2/down", 1.3),
            ("AT+TDC=120", 1.4),
            ("AT+BKDNS=1,0,66.33.22.220,33239", 1.5),
            ("AT+UNAME=dragino", 1.3),
            (f"AT+PWD={PASS}", 1.3),
            ("AT+CLIENT=ltc2-869181074162403", 1.3),
            ("AT+SERVADDR=66.33.22.220,33239", 1.4),
            ("AT+PUBTOPIC=dragino/ltc2/up", 1.3),
            ("AT+TDC=120", 1.3),
            ("AT+CFG", 4.5),
            ("AT+PUBTOPIC=?", 1.3),
            ("AT+TDC=?", 1.3),
            ("AT+UNAME=?", 1.3),
            ("AT+CLIENT=?", 1.3),
            ("AT+BKDNS=?", 1.3),
        ]:
            if not client.is_connected:
                log("SYS", "disconnected")
                return 3
            await send(cmd, wait)

        allrx = "".join(inbox)
        log("SYS", f"has_ltc2_up={('dragino/ltc2/up' in allrx)}")
        log("SYS", f"tdc_120_cfg={('AT+TDC=120' in allrx)}")
        log("SYS", f"tdc_query={('\\r\\n120\\r\\n' in allrx)}")
        log("SYS", f"uname={('AT+UNAME=dragino' in allrx)}")
        log("SYS", f"client_ok={('ltc2-869181074162403' in allrx)}")
        log("SYS", f"bkdns={('66.33.22.220' in allrx)}")
        still_tb = "AT+PUBTOPIC=v1/devices/me/telemetry" in allrx
        log("SYS", f"still_tb_pub={still_tb}")
        log("SYS", f"done log={logpath}")
        ok = ("dragino/ltc2/up" in allrx) and ("AT+TDC=120" in allrx or "\r\n120\r\n" in allrx)
        return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
