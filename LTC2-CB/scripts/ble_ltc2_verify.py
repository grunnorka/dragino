"""Verify LTC2-CB CFG after hardware ACT reset."""
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
ROOT = Path(__file__).resolve().parents[2]


def log_line(logpath: Path, tag: str, s: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    row = f"{ts} {tag} {s}"
    print(row, flush=True)
    with logpath.open("a", encoding="utf-8") as f:
        f.write(row + "\n")


async def amain() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_post_atz_verify.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        log_line(logpath, tag, s)

    print(">>> Short ACT 1-3s if BLE not up <<<", flush=True)
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=90.0,
    )
    if not device:
        log("SYS", "not advertising — press ACT 1-3s")
        return 1

    inbox: list[str] = []
    unlocked = asyncio.Event()

    def on_notify(_s, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        log("RX", text.replace("\r", "\\r").replace("\n", "\\n"))
        inbox.append(text)
        if "Password Correct" in text or "LTC2-CB" in text:
            unlocked.set()

    log("SYS", f"found {device}")
    async with BleakClient(device, timeout=30.0) as client:
        await client.start_notify(FFE1, on_notify)

        async def send(cmd: str, wait: float = 1.2) -> None:
            log("TX", cmd)
            try:
                await client.write_gatt_char(
                    FFE1, (cmd + "\r\n").encode("ascii"), response=True
                )
            except Exception:
                await client.write_gatt_char(
                    FFE1, (cmd + "\r\n").encode("ascii"), response=False
                )
            await asyncio.sleep(wait)

        # After >3s ACT, OTA window is ~12s — AT unlock is unreliable during it
        log("SYS", "waiting 15s for OTA window / modem settle...")
        await asyncio.sleep(15.0)

        await send("AT+MODEL=?", 1.5)
        if not unlocked.is_set():
            for _ in range(12):
                if unlocked.is_set() or not client.is_connected:
                    break
                await send(PIN, 0.4)
        if not unlocked.is_set():
            await send(f"AT+PIN={PIN}", 1.2)
            await send("AT+MODEL=?", 1.5)
        if not unlocked.is_set() and not any("LTC2-CB" in x for x in inbox):
            log("SYS", "unlock failed")
            return 2

        log("SYS", "console OK")
        await asyncio.sleep(1.0)
        for cmd, wait in [
            ("AT+CFG", 4.5),
            ("AT+SERVADDR=?", 1.4),
            ("AT+UNAME=?", 1.4),
            ("AT+BKDNS=?", 1.4),
            ("AT+PRO=?", 1.4),
            ("AT+TDC=?", 1.4),
            ("AT+APN=?", 1.4),
            ("AT+LDATA", 2.0),
        ]:
            if not client.is_connected:
                log("SYS", "disconnected")
                break
            await send(cmd, wait)

        allrx = "".join(inbox)
        checks = {
            "MODEL": "LTC2-CB" in allrx,
            "SERVADDR": "167.235.104.181,1883" in allrx and "AT+SERVADDR=NULL" not in allrx,
            "UNAME": TOKEN in allrx,
            "PRO": "3,3" in allrx,
            "TDC_1800": ("AT+TDC=1800" in allrx) or ("\r\n1800\r\n" in allrx),
            "BKDNS_IP": "167.235.104.181" in allrx,
            "APN": "lpwa.vodafone.is" in allrx,
            "no_HiveMQ": "broker.hivemq.com" not in allrx.lower(),
        }
        mqtt_fail = (
            "MQTT parameter configuration error" in allrx
            or "Failed to open the MQTT" in allrx
            or "Failed to send" in allrx
        )
        log("SYS", "--- verify ---")
        ok = True
        for key, passed in checks.items():
            log("SYS", f"{'PASS' if passed else 'FAIL'}: {key}")
            ok = ok and passed
        log("SYS", f"{'WARN' if mqtt_fail else 'OK  '}: mqtt_fail_seen={mqtt_fail}")
        log("SYS", f"done ok={ok} log={logpath}")
        return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
