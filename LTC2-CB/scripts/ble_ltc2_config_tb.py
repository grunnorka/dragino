"""Apply ThingsBoard MQTT to LTC2-CB over BLE (robust unlock + ATZ + verify)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
NAME = "869181074162403"
PIN = "358613"
TOKEN = "cdHsbYNjHJ7haAPkoJZD"
SERVADDR = "167.235.104.181,1883"
APN = "lpwa.vodafone.is"
ROOT = Path(__file__).resolve().parents[2]


def safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


async def find_device(name: str, timeout: float):
    safe_print(f">>> Hold ACT ~3s for {name} (timeout {timeout:.0f}s) <<<")
    return await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == name,
        timeout=timeout,
    )


class BleAt:
    def __init__(self, client: BleakClient, log) -> None:
        self.client = client
        self.log = log
        self.inbox: list[str] = []
        self._got_password = asyncio.Event()
        self._got_model = asyncio.Event()

    def on_notify(self, _s, data: bytearray) -> None:
        text = data.decode("utf-8", "replace")
        self.log("RX", text.replace("\r", "\\r").replace("\n", "\\n"))
        self.inbox.append(text)
        if "Password Correct" in text:
            self._got_password.set()
        if "LTC2-CB" in text:
            self._got_model.set()

    @property
    def connected(self) -> bool:
        return self.client.is_connected

    async def send(self, cmd: str, wait: float = 1.0) -> str:
        if not self.connected:
            raise BleakError("Not connected")
        self.log("TX", cmd)
        before = len(self.inbox)
        await self.client.write_gatt_char(
            FFE1, (cmd + "\r\n").encode("ascii"), response=False
        )
        await asyncio.sleep(wait)
        return "".join(self.inbox[before:])

    async def unlock(self, pin: str) -> bool:
        # If already unlocked, MODEL works without PIN
        await self.send("AT+MODEL=?", 1.2)
        if self._got_model.is_set() or any("LTC2-CB" in x for x in self.inbox):
            self.log("SYS", "already unlocked (MODEL ok)")
            return True

        # Rapid PIN (worked earlier); ignore AT_ERROR noise
        for i in range(10):
            if not self.connected:
                return False
            await self.send(pin, 0.35)
            if self._got_password.is_set():
                self.log("SYS", "unlock OK (Password Correct)")
                await asyncio.sleep(0.5)
                return True

        await self.send(f"AT+PIN={pin}", 1.0)
        if self._got_password.is_set():
            self.log("SYS", "unlock OK via AT+PIN")
            return True

        # Final check — maybe unlocked despite no banner
        await self.send("AT+MODEL=?", 1.2)
        if any("LTC2-CB" in x for x in self.inbox[-5:]):
            self.log("SYS", "unlock OK (MODEL after PIN)")
            return True
        self.log("SYS", "unlock FAILED")
        return False


async def run_phase(device, pin: str, log, phase: str, cmds: list[tuple[str, float]], do_atz: bool) -> str:
    async with BleakClient(device, timeout=30.0) as client:
        log("SYS", f"{phase}: connected={client.is_connected}")
        at = BleAt(client, log)
        await client.start_notify(FFE1, at.on_notify)
        await asyncio.sleep(0.4)

        if not await at.unlock(pin):
            return "".join(at.inbox)

        await asyncio.sleep(1.5)  # let modem chatter settle

        for cmd, wait in cmds:
            if not at.connected:
                log("SYS", f"{phase}: disconnected before {cmd}")
                break
            rx = await at.send(cmd, wait)
            # Soft retry once if no OK and not an informational Attention line only
            if cmd.startswith("AT+") and "OK" not in rx and "Attention" not in rx:
                await at.send(cmd, wait)

        if do_atz and at.connected:
            log("SYS", f"{phase}: ATZ")
            try:
                await at.send("ATZ", 1.0)
            except BleakError:
                log("SYS", f"{phase}: link dropped on ATZ (expected)")

        return "".join(at.inbox)


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--pin", default=PIN)
    ap.add_argument("--token", default=TOKEN)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_tb_config3.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {s}"
        safe_print(row)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    config_cmds = [
        ("AT+PRO=3,3", 2.5),
        (f"AT+SERVADDR={SERVADDR}", 1.8),
        (f"AT+UNAME={args.token}", 1.5),
        ("AT+PWD=NULL", 1.3),
        ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5),
        ("AT+SUBTOPIC=v1/devices/me/attributes", 1.5),
        ("AT+CLIENT=null", 1.3),
        ("AT+MQOS=1", 1.3),
        ("AT+TLSMOD=0,0", 1.3),
        ("AT+BKDNS=1,0,167.235.104.181,1883", 1.8),
        (f"AT+APN={APN}", 1.5),
        ("AT+CLOCKLOG=1,65535,5,8", 1.5),
        ("AT+TDC=1800", 1.5),
        # Re-assert after PRO side effects
        (f"AT+SERVADDR={SERVADDR}", 1.8),
        (f"AT+UNAME={args.token}", 1.5),
        ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.3),
        ("AT+SUBTOPIC=v1/devices/me/attributes", 1.3),
        ("AT+TDC=1800", 1.3),
        ("AT+MQOS=1", 1.3),
        ("AT+BKDNS=1,0,167.235.104.181,1883", 1.5),
    ]

    safe_print(f"LTC2 TB → {SERVADDR}  log={logpath}")
    d1 = await find_device(args.name, args.timeout)
    if not d1:
        log("SYS", "phase1: not found")
        return 1
    await run_phase(d1, args.pin, log, "phase1", config_cmds, do_atz=True)

    safe_print("\nATZ sent. Wait ~12s, then hold ACT ~3s for verify/repair...\n")
    await asyncio.sleep(12.0)

    repair_verify = [
        ("AT+PRO=3,3", 2.5),
        (f"AT+SERVADDR={SERVADDR}", 1.8),
        (f"AT+UNAME={args.token}", 1.5),
        ("AT+PWD=NULL", 1.3),
        ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.4),
        ("AT+SUBTOPIC=v1/devices/me/attributes", 1.4),
        ("AT+CLIENT=null", 1.3),
        ("AT+MQOS=1", 1.3),
        ("AT+TLSMOD=0,0", 1.3),
        ("AT+BKDNS=1,0,167.235.104.181,1883", 1.8),
        (f"AT+APN={APN}", 1.5),
        ("AT+CLOCKLOG=1,65535,5,8", 1.4),
        ("AT+TDC=1800", 1.4),
        ("AT+CFG", 4.5),
        ("AT+SERVADDR=?", 1.4),
        ("AT+UNAME=?", 1.4),
        ("AT+TDC=?", 1.4),
        ("AT+BKDNS=?", 1.4),
        ("AT+PRO=?", 1.4),
        ("AT+APN=?", 1.4),
    ]

    d2 = await find_device(args.name, args.timeout)
    if not d2:
        log("SYS", "phase2: not found after ATZ")
        return 2
    rx = await run_phase(d2, args.pin, log, "phase2", repair_verify, do_atz=False)

    checks = {
        "MODEL": "LTC2-CB" in rx,
        "PRO": "AT+PRO=3,3" in rx,
        "SERVADDR": f"AT+SERVADDR={SERVADDR}" in rx or ("167.235.104.181,1883" in rx and "AT+SERVADDR=NULL" not in rx),
        "UNAME": args.token in rx,
        "PUB": "v1/devices/me/telemetry" in rx,
        "SUB": "v1/devices/me/attributes" in rx,
        "TDC": "AT+TDC=1800" in rx or "\r\n1800\r\n" in rx,
        "BKDNS": "167.235.104.181" in rx,
        "APN": APN in rx,
        "no_HiveMQ": "broker.hivemq.com" not in rx.lower(),
        "CLOCKLOG": "1,65535,5,8" in rx,
    }
    # Stronger SERVADDR fail if CFG still NULL
    if "AT+SERVADDR=NULL" in rx:
        checks["SERVADDR"] = False
    if "AT+UNAME=NULL" in rx:
        checks["UNAME"] = False
    if "AT+TDC=7200" in rx and "AT+TDC=1800" not in rx:
        checks["TDC"] = False

    log("SYS", "--- verify ---")
    ok = True
    for k, v in checks.items():
        log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
        ok = ok and v
    log("SYS", f"done ok={ok} log={logpath}")
    return 0 if ok else 4


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(amain()))
    except BleakError as e:
        safe_print(f"BLE error: {e}")
        sys.exit(1)
