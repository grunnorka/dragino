"""Configure LTC2-CB for Railway MQTT over BLE.

Default: apply + CFG verify (no ATZ).
Use --atz-tdc after SERVADDR/BKDNS verified to persist TDC=120 (needs ACT twice).
"""
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
USER = "dragino"
PASS = "DrgN0-MqTt-7kR9wX2pL"
CLIENT = f"ltc2-{NAME}"
HOST_IP = "66.33.22.220"
PORT = 33239
PUB = "dragino/ltc2/up"
SUB = "dragino/ltc2/down"
TDC = 120
ROOT = Path(__file__).resolve().parents[2]


def redacted(s: str) -> str:
    return s.replace(PASS, "***")


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--atz-tdc",
        action="store_true",
        help="After Railway CFG verify, set TDC=120, ATZ, reconnect, re-pin, re-verify",
    )
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_ltc2_config_railway.raw.log"
    logpath.parent.mkdir(exist_ok=True)

    def log(tag: str, s: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {redacted(s)}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    print(
        "\n========================================================\n"
        "  LTC2-CB Railway MQTT — HOLD ACT 1-3s NOW\n"
        "  (Do NOT hold ~12s / OTA)\n"
        f"  BLE name: {NAME}\n"
        f"  SERVADDR={HOST_IP},{PORT}  CLIENT={CLIENT}\n"
        f"  topics {PUB} / {SUB}  PRO=3,5  TLS=off\n"
        f"  atz_tdc={args.atz_tdc}\n"
        "========================================================\n",
        flush=True,
    )

    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=args.timeout,
    )
    if not device:
        log("SYS", "not advertising — press ACT 1-3s")
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

        log("SYS", f"{label}: wait console ready (max 18s)")
        try:
            await asyncio.wait_for(saw_rx.wait(), timeout=18.0)
            await asyncio.sleep(1.5)
        except asyncio.TimeoutError:
            log("SYS", f"{label}: no spontaneous RX; settle 10s then unlock")
            await asyncio.sleep(10.0)

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
        if not client.is_connected:
            raise RuntimeError("BLE disconnected")
        log("TX", cmd)
        await client.write_gatt_char(
            FFE1, (cmd + "\r\n").encode("ascii"), response=False
        )
        await asyncio.sleep(wait)

    async def wait_upload_idle(max_s: float = 90.0) -> None:
        """Avoid writing AT while modem upload is active (drops BLE)."""
        deadline = asyncio.get_event_loop().time() + max_s
        while asyncio.get_event_loop().time() < deadline:
            recent = "".join(inbox[-8:])
            if "Upload start" in recent and "End of upload" not in recent:
                log("SYS", "upload in progress — waiting...")
                await asyncio.sleep(3.0)
                continue
            # also wait a beat after end
            if "End of upload" in recent:
                await asyncio.sleep(2.0)
            return

    def verify(allrx: str) -> dict[str, bool]:
        checks = {
            "PRO_3_5": "AT+PRO=3,5" in allrx,
            "SERVADDR": HOST_IP in allrx and "broker.hivemq.com" not in allrx.lower(),
            "BKDNS": HOST_IP in allrx,
            "UNAME": f"AT+UNAME={USER}" in allrx or f"\r\n{USER}\r\n" in allrx,
            "CLIENT": CLIENT in allrx and "AT+CLIENT=null" not in allrx,
            "PUB": PUB in allrx,
            "SUB": SUB in allrx,
            "TLS_off": "AT+TLSMOD=0,0" in allrx,
            "no_HiveMQ": "broker.hivemq.com" not in allrx.lower(),
            "no_TB_pub": "v1/devices/me/telemetry" not in allrx,
        }
        if "AT+CLIENT=null" in allrx:
            checks["CLIENT"] = False
        if "AT+SERVADDR=broker.hivemq.com" in allrx:
            checks["SERVADDR"] = False
            checks["no_HiveMQ"] = False
        return checks

    apply_cmds = [
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

    # ---- Phase 1: apply Railway, verify (no ATZ yet) ----
    async with BleakClient(device, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock(client, "phase1"):
            return 2

        await wait_upload_idle(90.0)

        for cmd, wait in apply_cmds:
            if not client.is_connected:
                log("SYS", "disconnected during apply — will try reconnect verify")
                break
            try:
                await send_on(client, cmd, wait)
            except Exception as exc:  # noqa: BLE001
                log("SYS", f"apply write err: {exc}")
                break
            # pause if upload starts mid-apply
            recent = "".join(inbox[-3:])
            if "Upload start" in recent:
                await wait_upload_idle(90.0)

        if client.is_connected:
            allrx = "".join(inbox)
            checks = verify(allrx)
            tdc_ok = f"AT+TDC={TDC}" in allrx or f"\r\n{TDC}\r\n" in allrx
            checks["TDC_120"] = tdc_ok
            if "AT+TDC=1800" in allrx and not tdc_ok:
                checks["TDC_120"] = False

            log("SYS", "--- verify (pre-ATZ) ---")
            core_ok = True
            for k, v in checks.items():
                if k == "TDC_120":
                    log("SYS", f"{'PASS' if v else 'WARN'}: {k}")
                    continue
                log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
                core_ok = core_ok and v

            if not core_ok:
                # still try CFG if we never got it
                if "AT+CFG" not in "".join(x for x in inbox if x.startswith("")):
                    pass
                if "AT+MODEL=LTC2-CB" not in allrx and "AT+SERVADDR=" not in allrx:
                    log("SYS", "CFG incomplete — reconnect for verify dump")
                    core_ok = False
            else:
                log("SYS", "Railway SERVADDR/BKDNS/auth verified OK")

            if core_ok and not args.atz_tdc:
                log("SYS", f"done (no ATZ) ok={core_ok} tdc120={tdc_ok} log={logpath}")
                return 0

            if core_ok and args.atz_tdc:
                log("SYS", "TDC persist: AT+TDC=120 then ATZ")
                await wait_upload_idle(60.0)
                try:
                    await send_on(client, f"AT+TDC={TDC}", 1.4)
                    await send_on(client, "ATZ", 1.5)
                except Exception as exc:  # noqa: BLE001
                    log("SYS", f"ATZ drop (expected): {exc}")
            elif not core_ok:
                log("SYS", "core verify incomplete this session; reconnect phase")
        else:
            log("SYS", "BLE dropped after apply TX — reconnect for CFG")

    # Reconnect if CFG dump missing or ATZ not yet sent
    log_text = logpath.read_text(encoding="utf-8") if logpath.exists() else ""
    did_atz = " TX ATZ" in log_text
    saw_cfg = "AT+MODEL=LTC2-CB" in log_text or "AT+DEUI=" in log_text

    if not saw_cfg or (args.atz_tdc and not did_atz):
        print(
            "\n========================================================\n"
            "  HOLD ACT 1-3s AGAIN — reconnect to finish CFG / ATZ\n"
            "========================================================\n",
            flush=True,
        )
        await asyncio.sleep(3.0)
        inbox.clear()
        unlocked.clear()
        saw_rx.clear()
        device_r = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name or ad.local_name or "") == NAME,
            timeout=args.timeout,
        )
        if not device_r:
            log("SYS", "reconnect: not advertising")
            return 5
        async with BleakClient(device_r, timeout=35.0) as client:
            await client.start_notify(FFE1, on_notify)
            if not await unlock(client, "reconnect"):
                return 2
            await wait_upload_idle(90.0)
            for cmd, wait in apply_cmds:
                if not client.is_connected:
                    break
                try:
                    await send_on(client, cmd, wait)
                except Exception as exc:  # noqa: BLE001
                    log("SYS", f"reconnect apply err: {exc}")
                    break
                if "Upload start" in "".join(inbox[-3:]):
                    await wait_upload_idle(90.0)

            allrx = "".join(inbox)
            checks = verify(allrx)
            tdc_ok = f"AT+TDC={TDC}" in allrx or f"\r\n{TDC}\r\n" in allrx
            checks["TDC_120"] = tdc_ok
            log("SYS", "--- verify (reconnect) ---")
            core_ok = True
            for k, v in checks.items():
                if k == "TDC_120":
                    log("SYS", f"{'PASS' if v else 'WARN'}: {k}")
                    continue
                log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
                core_ok = core_ok and v
            if not core_ok:
                log("SYS", f"Railway CFG verify FAIL. log={logpath}")
                return 4
            log("SYS", "Railway verified after reconnect")
            if args.atz_tdc:
                await wait_upload_idle(60.0)
                log("SYS", "TDC persist: AT+TDC=120 then ATZ")
                try:
                    await send_on(client, f"AT+TDC={TDC}", 1.4)
                    await send_on(client, "ATZ", 1.5)
                except Exception as exc:  # noqa: BLE001
                    log("SYS", f"ATZ drop (expected): {exc}")
            else:
                log("SYS", f"done ok=True log={logpath}")
                return 0

    if not args.atz_tdc:
        log("SYS", f"done log={logpath}")
        return 0

    print(
        "\n========================================================\n"
        "  REBOOTING — wait ~12s, then HOLD ACT 1-3s AGAIN\n"
        "  for Railway re-pin + CFG verify\n"
        "========================================================\n",
        flush=True,
    )
    await asyncio.sleep(12.0)

    # ---- Phase 2: re-pin after ATZ ----
    inbox.clear()
    unlocked.clear()
    saw_rx.clear()

    device2 = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or ad.local_name or "") == NAME,
        timeout=args.timeout,
    )
    if not device2:
        log("SYS", "phase2: not advertising — press ACT again")
        return 5

    async with BleakClient(device2, timeout=35.0) as client:
        await client.start_notify(FFE1, on_notify)
        if not await unlock(client, "phase2"):
            return 2
        await wait_upload_idle(90.0)

        for cmd, wait in apply_cmds:
            if not client.is_connected:
                log("SYS", "disconnected during phase2")
                return 3
            try:
                await send_on(client, cmd, wait)
            except Exception as exc:  # noqa: BLE001
                log("SYS", f"phase2 err: {exc}")
                return 3
            if "Upload start" in "".join(inbox[-3:]):
                await wait_upload_idle(90.0)

        allrx = "".join(inbox)
        checks = verify(allrx)
        tdc_ok = f"AT+TDC={TDC}" in allrx or f"\r\n{TDC}\r\n" in allrx
        checks["TDC_120"] = tdc_ok
        if "AT+TDC=1800" in allrx and not tdc_ok:
            checks["TDC_120"] = False

        log("SYS", "--- verify (post-ATZ) ---")
        ok = True
        for k, v in checks.items():
            log("SYS", f"{'PASS' if v else 'FAIL'}: {k}")
            ok = ok and v
        uploadish = any(
            x in allrx.lower()
            for x in ("upload start", "upload data successfully", "mqtt")
        )
        log("SYS", f"upload_chatter_seen={uploadish}")
        log("SYS", f"done ok={ok} log={logpath}")
        return 0 if ok else 6


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
