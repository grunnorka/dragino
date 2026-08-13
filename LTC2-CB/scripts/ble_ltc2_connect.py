"""Scan/connect Dragino LTC2-CB over BLE and unlock ASAP before AT+PWRM2 sleep."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
FFE2 = "0000ffe2-0000-1000-8000-00805f9b34fb"


def safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="869181074162403")
    ap.add_argument("--pin", default="358613")
    ap.add_argument("--timeout", type=float, default=75.0)
    args = ap.parse_args()

    pin = args.pin
    safe_print(
        f"Waiting for {args.name!r} (timeout {args.timeout:.0f}s).\n"
        ">>> Hold ACT ~3s NOW (green blinks) <<<"
    )
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == args.name,
        timeout=args.timeout,
    )
    if not device:
        safe_print("Not advertising. Hold ACT ~3s and retry.")
        return 1

    inbox: list[str] = []
    unlocked = asyncio.Event()

    def on_notify(_s, data: bytearray) -> None:
        # Keep raw hex for binary; text when printable
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError:
            text = data.decode("utf-8", "replace")
            safe_print(f"<< BIN {data.hex()} {text!r}")
            inbox.append(text)
            return
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        safe_print(f"[{ts}] << {text!r}")
        inbox.append(text)
        if "Password Correct" in text:
            unlocked.set()

    safe_print(f"Found {device}; connecting...")
    async with BleakClient(device, timeout=30.0) as client:
        safe_print(f"Connected={client.is_connected}")
        await client.start_notify(FFE1, on_notify)

        async def blast(payload: bytes, note: str, char: str = FFE1) -> None:
            if not client.is_connected:
                return
            safe_print(f">> [{note}] {payload!r}")
            try:
                await client.write_gatt_char(char, payload, response=False)
            except Exception as exc:  # noqa: BLE001
                safe_print(f"   write err: {exc}")

        # Immediate rapid unlock — MCU may send AT+PWRM2 and kill BLE soon
        for i in range(8):
            if unlocked.is_set() or not client.is_connected:
                break
            await blast(f"{pin}\r\n".encode("ascii"), f"PIN CRLF #{i+1}")
            await asyncio.sleep(0.25)
            if unlocked.is_set():
                break
            await blast(f"{pin}\n".encode("ascii"), f"PIN LF #{i+1}")
            await asyncio.sleep(0.25)

        if not unlocked.is_set() and client.is_connected:
            await blast(f"AT+PIN={pin}\r\n".encode("ascii"), "AT+PIN")
            await asyncio.sleep(0.5)
            await blast(f"{pin}\r\n".encode("ascii"), "PIN ffe2", FFE2)

        # Hold open briefly for Password Correct / CFG
        deadline = asyncio.get_event_loop().time() + 8.0
        while asyncio.get_event_loop().time() < deadline:
            if unlocked.is_set():
                break
            if not client.is_connected:
                safe_print("Link dropped.")
                break
            await asyncio.sleep(0.2)

        if unlocked.is_set() and client.is_connected:
            safe_print("UNLOCK OK — probing")
            for cmd in ("AT+MODEL=?", "AT+CGSN", "AT+GETSENSORVALUE"):
                await blast(f"{cmd}\r\n".encode("ascii"), cmd)
                await asyncio.sleep(1.5)
        else:
            safe_print("Unlock failed.")
            # summarize RX
            for line in inbox[:20]:
                safe_print(f"  seen: {line!r}")

        safe_print(f"done unlocked={unlocked.is_set()} chunks={len(inbox)}")
        return 0 if unlocked.is_set() else 2


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(amain()))
    except BleakError as e:
        safe_print(f"BLE error: {e}")
        sys.exit(1)
