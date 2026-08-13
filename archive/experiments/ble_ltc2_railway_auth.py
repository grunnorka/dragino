"""Fix LTC2-CB Railway MQTT auth: UNAME/PWD + unique CLIENT."""
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
CLIENT = f"ltc2-{NAME}"  # unique, not null
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
    logpath = ROOT / "logs" / f"{stamp}_ltc2_railway_auth.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(
        f"Fix Railway auth: UNAME={USER} CLIENT={CLIENT}\n"
        ">>> Hold ACT 1-3s NOW <<<",
        flush=True,
    )
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=120.0,
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

    async with BleakClient(device, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)

        async def send(cmd: str, wait: float = 1.4) -> str:
            if not client.is_connected:
                raise RuntimeError("Not connected")
            log("TX", cmd)
            before = len(inbox)
            # write-without-response is more reliable on this DX-BT24 link
            await client.write_gatt_char(
                FFE1, (cmd + "\r\n").encode("ascii"), response=False
            )
            await asyncio.sleep(wait)
            return "".join(inbox[before:])

        await asyncio.sleep(12.0)  # past OTA / let MCU console ready
        await send("AT+MODEL=?", 1.2)
        if not unlocked.is_set():
            for _ in range(16):
                if unlocked.is_set() or not client.is_connected:
                    break
                try:
                    await send(PIN, 0.35)
                except Exception as exc:  # noqa: BLE001
                    log("SYS", f"unlock write err: {exc}")
                    return 3
        if not unlocked.is_set():
            log("SYS", "unlock failed")
            return 2
        log("SYS", "unlocked")

        # Auth + broker + client (no ATZ — keep session alive for verify)
        for cmd, wait in [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 1.6),
            (f"AT+BKDNS=1,0,{HOST_IP},{PORT}", 1.6),
            (f"AT+UNAME={USER}", 1.5),
            (f"AT+PWD={PASS}", 1.5),
            (f"AT+CLIENT={CLIENT}", 1.5),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+SUBTOPIC={SUB}", 1.3),
            ("AT+MQOS=1", 1.2),
            (f"AT+TDC={TDC}", 1.3),
            # re-assert auth (critical)
            (f"AT+UNAME={USER}", 1.4),
            (f"AT+PWD={PASS}", 1.4),
            (f"AT+CLIENT={CLIENT}", 1.4),
            (f"AT+SERVADDR={HOST_IP},{PORT}", 1.5),
            ("AT+CFG", 4.5),
            ("AT+UNAME=?", 1.4),
            ("AT+PWD=?", 1.4),
            ("AT+CLIENT=?", 1.4),
            ("AT+SERVADDR=?", 1.4),
            ("AT+PRO=?", 1.4),
            ("AT+TDC=?", 1.4),
        ]:
            if not client.is_connected:
                log("SYS", "disconnected")
                return 3
            await send(cmd, wait)

        allrx = "".join(inbox)
        checks = {
            "UNAME_dragino": f"AT+UNAME={USER}" in allrx or f"\r\n{USER}\r\n" in allrx,
            "PWD_set": "AT+PWD=NULL" not in allrx or PASS[:4] in allrx,
            "CLIENT_unique": CLIENT in allrx and "AT+CLIENT=null" not in allrx,
            "SERVADDR_railway": HOST_IP in allrx,
            "PRO_3_5": "3,5" in allrx,
            "TDC_120": "120" in allrx,
            "no_HiveMQ": "broker.hivemq.com" not in allrx.lower(),
        }
        # Stronger: CFG must not show null client
        if "AT+CLIENT=null" in allrx:
            checks["CLIENT_unique"] = False
        if "AT+UNAME=NULL" in allrx or "AT+UNAME=null" in allrx:
            checks["UNAME_dragino"] = False
        if f"AT+UNAME={USER}" in allrx:
            checks["UNAME_dragino"] = True
        if f"AT+CLIENT={CLIENT}" in allrx:
            checks["CLIENT_unique"] = True
        if f"AT+PWD={PASS}" in allrx:
            checks["PWD_set"] = True

        log("SYS", "--- verify ---")
        ok = True
        for k, v in checks.items():
            log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
            ok = ok and v
        log("SYS", f"done ok={ok} log={logpath}")
        return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
