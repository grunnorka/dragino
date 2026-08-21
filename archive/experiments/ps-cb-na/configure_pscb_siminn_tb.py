#!/usr/bin/env python3
"""Configure PS-CB-NA for Síminn SIM + vakt.systemat.is (ThingsBoard MQTT).

Unlocks the device on wake (stable policy), reads current config, applies the
ThingsBoard profile, and reboots with ATZ so the settings persist.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du

APN = "internet"
SERVADDR = "167.235.104.181,1883"
BKDNS = "1,0,167.235.104.181,1883"
PUB = "v1/devices/me/telemetry"
SUB = "v1/devices/me/attributes"
CLIENT = "null"
MQOS = "1"
TLSMOD = "0,0"
TDC = "86400"  # one upload per day
CLOCKLOG = "1,65535,360,4"  # sample every 6 h, keep 4 slots (24 h coverage)


def redact(text: str, pin: str, token: str) -> str:
    if pin:
        text = text.replace(pin, "***PIN***")
    if token:
        text = text.replace(token, "***TOKEN***")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB1"))
    parser.add_argument("--token", default=os.environ.get("TB_TOKEN"), help="ThingsBoard device access token (or set TB_TOKEN env)")
    args = parser.parse_args()
    if not args.token:
        print("ERROR: Provide --token or set TB_TOKEN", file=sys.stderr)
        return 2

    du.load_dotenv(ROOT / ".env")
    pin = du.resolve_pin()
    if not pin:
        print("ERROR: No DRAGINO_PIN found in .env", file=sys.stderr)
        return 2
    if not pin:
        print("ERROR: Set DRAGINO_PIN in .env", file=sys.stderr)
        return 2

    def out(text: str) -> None:
        print(redact(text, pin, args.token), flush=True)

    print("Waiting for device to wake and unlock (press ACT 1-3s or wait for TDC)...")
    ser = None
    result = None
    unlock_deadline = time.monotonic() + 240.0
    while time.monotonic() < unlock_deadline:
        try:
            if ser is None:
                print(f"Opening {args.port} 9600 8N1...")
                ser = du.open_serial(args.port, 9600)
            result = du.unlock(ser, pin, policy="stable", timeout=30.0, on_line=out, on_tx=out)
            if result.ok:
                break
            print(f"Unlock attempt failed: {result.hint}, retrying...")
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(1.0)
        except serial.SerialException as e:
            print(f"Port disconnect: {e}, retrying...")
            try:
                if ser:
                    ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(1.0)

    if not result or not result.ok:
        print("Failed to unlock after retries")
        return 2

    print(f"Unlock result: ok={result.ok} phase={result.phase.value} hint={result.hint}")
    buf = du.LineBuffer()

    print("\n=== Current config (redacted) ===")
    for cmd in ["AT+CFG", "AT+PRO=?", "AT+SERVADDR=?", "AT+APN=?", "AT+TDC=?", "AT+BKDNS=?", "AT+CLOCKLOG=?"]:
        print(f">>> {cmd}")
        du.send_line(ser, cmd)
        du.read_for(ser, 2.5, buf, out)

    def send_cmd(cmd: str, wait: float = 2.0) -> None:
        shown = cmd
        if cmd.startswith(f"AT+UNAME={args.token}"):
            shown = "AT+UNAME=***TOKEN***"
        print(f">>> {shown}")
        try:
            du.send_line(ser, cmd)
            du.read_for(ser, wait, buf, out)
        except serial.SerialException as e:
            print(f"Disconnect during {shown}: {e}")
            raise

    print("\n=== Applying Síminn + vakt.systemat.is config ===")
    # Use PRO=3,5 (JSON MQTT) on PS-CB-NA; PRO=3,3 reverts SERVADDR to broker.hivemq.com on reboot.
    for cmd in [
        "AT+PRO=3,5",
        f"AT+SERVADDR={SERVADDR}",
        f"AT+UNAME={args.token}",
        "AT+PWD=NULL",
        f"AT+PUBTOPIC={PUB}",
        f"AT+SUBTOPIC={SUB}",
        f"AT+CLIENT={CLIENT}",
        f"AT+MQOS={MQOS}",
        f"AT+TLSMOD={TLSMOD}",
        f"AT+BKDNS={BKDNS}",
        f"AT+APN={APN}",
        f"AT+TDC={TDC}",
        f"AT+CLOCKLOG={CLOCKLOG}",
    ]:
        send_cmd(cmd, 2.0)

    print("\n=== Verify post-apply ===")
    for cmd in ["AT+CFG", "AT+SERVADDR=?", "AT+PRO=?", "AT+APN=?", "AT+BKDNS=?", "AT+TDC=?", "AT+CLOCKLOG=?"]:
        print(f">>> {cmd}")
        du.send_line(ser, cmd)
        du.read_for(ser, 2.5, buf, out)

    print("\n=== Reboot with ATZ to persist ===")
    du.send_line(ser, "ATZ")
    du.read_for(ser, 2.0, buf, out)

    print("\nConfig saved. The device will reboot and reconnect with Síminn + vakt.systemat.is.")
    ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
