"""Set LTC2-CB TDC to a short cycle over BLE."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
ROOT = Path(__file__).resolve().parent


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdc", type=int, default=120, help="TDC seconds (default 120)")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_tdc.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {s}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(
        f"Set TDC={args.tdc}s ({args.tdc/60:.1f} min)\n"
        ">>> Hold ACT 1-3s NOW <<<",
        flush=True,
    )
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=args.timeout,
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

        async def send(cmd: str, wait: float = 1.0) -> str:
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

        # Brief settle; keep short so we don't miss the BLE window
        await asyncio.sleep(2.0)
        await send("AT+MODEL=?", 1.0)
        if not unlocked.is_set():
            for _ in range(10):
                if unlocked.is_set():
                    break
                await send(PIN, 0.3)
        if not unlocked.is_set():
            log("SYS", "unlock failed")
            return 2

        log("SYS", "unlocked")
        await send(f"AT+TDC={args.tdc}", 1.2)
        rx = await send("AT+TDC=?", 1.2)
        ok = str(args.tdc) in rx or any(str(args.tdc) in x for x in inbox[-5:])
        # Also peek SERVADDR while we're here
        await send("AT+SERVADDR=?", 1.2)
        allrx = "".join(inbox)
        log("SYS", f"TDC_set_ok={ok}")
        if "broker.hivemq.com" in allrx.lower():
            log("SYS", "WARN: SERVADDR still on HiveMQ")
        log("SYS", f"done log={logpath}")
        return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
