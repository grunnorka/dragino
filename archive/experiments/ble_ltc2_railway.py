"""Configure LTC2-CB for Railway MQTT over BLE (no ATZ)."""
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

HOST = "altaria.proxy.rlwy.net"
PORT = 33239
FALLBACK_IP = "66.33.22.220"
USER = "dragino"
PASS = "DrgN0-MqTt-7kR9wX2pL"
CLIENT = f"ltc2-{NAME}"  # unique; never null (broker rejects Client null)
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
TDC = 120

ROOT = Path(__file__).resolve().parent


def redacted(s: str) -> str:
    return s.replace(PASS, "***")


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-ip", action="store_true", default=True, help="SERVADDR=fallback IP (default)")
    ap.add_argument("--use-host", action="store_true", help="SERVADDR=hostname instead of IP")
    ap.add_argument("--tdc", type=int, default=TDC)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()
    use_ip = not args.use_host
    target = f"{FALLBACK_IP},{PORT}" if use_ip else f"{HOST},{PORT}"
    bkdns = f"1,0,{FALLBACK_IP},{PORT}"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_railway.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(
        f"LTC2 -> Railway MQTT {target}  PRO=3,5  TDC={args.tdc}s  TLS=off\n"
        f"topics {PUB} / {SUB}  CLIENT={CLIENT}\n"
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

        async def send(cmd: str, wait: float = 1.3) -> str:
            log("TX", cmd)
            before = len(inbox)
            # write-without-response is more reliable on DX-BT24 FFE1
            await client.write_gatt_char(
                FFE1, (cmd + "\r\n").encode("ascii"), response=False
            )
            await asyncio.sleep(wait)
            return "".join(inbox[before:])

        await asyncio.sleep(10.0)  # past OTA / console ready (2s was too short)
        await send("AT+MODEL=?", 1.2)
        if not unlocked.is_set():
            for _ in range(16):
                if unlocked.is_set():
                    break
                await send(PIN, 0.35)
        if not unlocked.is_set():
            log("SYS", "unlock failed")
            return 2
        log("SYS", "unlocked")

        # Core Railway MQTT (PRO=3,5 JSON). Re-assert SERVADDR after PRO.
        apply = [
            ("AT+PRO=3,5", 2.5),
            ("AT+TLSMOD=0,0", 1.5),
            (f"AT+SERVADDR={target}", 1.8),
            (f"AT+BKDNS={bkdns}", 1.8),
            (f"AT+UNAME={USER}", 1.4),
            (f"AT+PWD={PASS}", 1.4),
            (f"AT+PUBTOPIC={PUB}", 1.4),
            (f"AT+SUBTOPIC={SUB}", 1.4),
            (f"AT+CLIENT={CLIENT}", 1.3),
            ("AT+MQOS=1", 1.3),
            (f"AT+TDC={args.tdc}", 1.4),
            (f"AT+SERVADDR={target}", 1.8),
            (f"AT+BKDNS={bkdns}", 1.8),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={PASS}", 1.3),
            (f"AT+CLIENT={CLIENT}", 1.3),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+TDC={args.tdc}", 1.3),
        ]
        for cmd, wait in apply:
            if not client.is_connected:
                log("SYS", "disconnected during apply")
                break
            await send(cmd, wait)

        # PRO/BKDNS often need ATZ — then immediately re-pin Railway (no HiveMQ)
        if client.is_connected:
            log("SYS", "ATZ for PRO/BKDNS")
            try:
                await send("ATZ", 1.5)
            except Exception as exc:  # noqa: BLE001
                log("SYS", f"ATZ drop (expected): {exc}")

    # Phase 2 after reboot
    print("\nRebooting — wait 15s then hold ACT 1-3s for repair/verify...\n", flush=True)
    await asyncio.sleep(15.0)

    device2 = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=args.timeout,
    )
    if not device2:
        log("SYS", "phase2: not advertising")
        return 2

    inbox.clear()
    unlocked.clear()

    async with BleakClient(device2, timeout=30.0) as client:
        await client.start_notify(FFE1, on_notify)

        async def send(cmd: str, wait: float = 1.3) -> str:
            log("TX", cmd)
            before = len(inbox)
            await client.write_gatt_char(
                FFE1, (cmd + "\r\n").encode("ascii"), response=False
            )
            await asyncio.sleep(wait)
            return "".join(inbox[before:])

        await asyncio.sleep(12.0)  # past OTA window
        await send("AT+MODEL=?", 1.2)
        if not unlocked.is_set():
            for _ in range(16):
                if unlocked.is_set():
                    break
                await send(PIN, 0.35)
        if not unlocked.is_set():
            log("SYS", "phase2 unlock failed")
            return 2
        log("SYS", "phase2 unlocked — repair Railway pins")

        repair = [
            ("AT+PRO=3,5", 2.0),
            ("AT+TLSMOD=0,0", 1.3),
            (f"AT+SERVADDR={target}", 1.8),
            (f"AT+BKDNS={bkdns}", 1.8),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={PASS}", 1.3),
            (f"AT+PUBTOPIC={PUB}", 1.3),
            (f"AT+SUBTOPIC={SUB}", 1.3),
            (f"AT+CLIENT={CLIENT}", 1.2),
            ("AT+MQOS=1", 1.2),
            (f"AT+TDC={args.tdc}", 1.3),
            (f"AT+SERVADDR={target}", 1.5),
            (f"AT+BKDNS={bkdns}", 1.5),
            (f"AT+UNAME={USER}", 1.3),
            (f"AT+PWD={PASS}", 1.3),
            (f"AT+CLIENT={CLIENT}", 1.3),
            ("AT+CFG", 4.5),
            ("AT+SERVADDR=?", 1.4),
            ("AT+BKDNS=?", 1.4),
            ("AT+PRO=?", 1.4),
            ("AT+TDC=?", 1.4),
            ("AT+UNAME=?", 1.4),
            ("AT+CLIENT=?", 1.4),
            ("AT+PUBTOPIC=?", 1.4),
            ("AT+TLSMOD=?", 1.4),
        ]
        for cmd, wait in repair:
            if not client.is_connected:
                log("SYS", "disconnected during repair")
                break
            await send(cmd, wait)

        allrx = "".join(inbox)
        host_key = FALLBACK_IP if use_ip else HOST
        checks = {
            "PRO_3_5": "AT+PRO=3,5" in allrx or "3,5\r" in allrx,
            "SERVADDR": host_key in allrx and "broker.hivemq.com" not in allrx.lower(),
            "BKDNS_IP": FALLBACK_IP in allrx,
            "UNAME": USER in allrx,
            "CLIENT": CLIENT in allrx and "AT+CLIENT=null" not in allrx,
            "PUB": PUB in allrx,
            "TDC": f"AT+TDC={args.tdc}" in allrx or f"\r\n{args.tdc}\r\n" in allrx,
            "TLS_off": "AT+TLSMOD=0,0" in allrx,
            "no_HiveMQ": "broker.hivemq.com" not in allrx.lower(),
            "no_TB_IP": "167.235.104.181" not in allrx,
        }
        if "AT+CLIENT=null" in allrx:
            checks["CLIENT"] = False
        if f"AT+TDC={args.tdc}" not in allrx and f"\r\n{args.tdc}\r\n" not in allrx:
            # CFG still showing old TDC (e.g. 1800) after set+ATZ
            if f"AT+TDC=1800" in allrx:
                checks["TDC"] = False
        if "AT+SERVADDR=broker.hivemq.com" in allrx:
            checks["SERVADDR"] = False
            checks["no_HiveMQ"] = False

        log("SYS", "--- verify ---")
        ok = True
        for k, v in checks.items():
            log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
            ok = ok and v
        log("SYS", f"done ok={ok} log={logpath}")
        return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
