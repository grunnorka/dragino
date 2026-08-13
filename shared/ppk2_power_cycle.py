"""Control Nordic PPK2 DUT power for Dragino PS-CB-NA restarts.

PPK2 USB: VID_1915 / PID_C00A
  COM10 = PPK2 control (this script)  — MI_01
  COM9  = PPK2 secondary CDC          — MI_03 (unused here)
  COM8  = Dragino sensor FTDI UART    — DO NOT use for PPK2

Sensor supply rating (manual): 2.6 V .. 3.6 V  -> default 3.3 V
"""
from __future__ import annotations

import argparse
import sys
import time

from ppk2_api.ppk2_api import PPK2_API

DEFAULT_PORT = "COM10"
DEFAULT_MV = 3300


def open_ppk(port: str) -> PPK2_API:
    ppk = PPK2_API(port, timeout=1, write_timeout=1)
    ppk.get_modifiers()
    ppk.use_source_meter()
    time.sleep(0.15)
    return ppk


def main() -> int:
    p = argparse.ArgumentParser(description="PPK2 source-meter DUT power control")
    p.add_argument("--port", default=DEFAULT_PORT, help="PPK2 serial port (default COM10)")
    p.add_argument("--voltage-mv", type=int, default=DEFAULT_MV, help="Source voltage in mV (800-5000)")
    p.add_argument(
        "action",
        choices=["on", "off", "cycle", "status-setup"],
        help="on | off | cycle (off then on) | status-setup (source meter + voltage only)",
    )
    p.add_argument("--off-seconds", type=float, default=2.0, help="Seconds OFF during cycle")
    args = p.parse_args()

    if not (800 <= args.voltage_mv <= 5000):
        print("voltage-mv must be 800..5000", file=sys.stderr)
        return 2
    if args.voltage_mv > 3600:
        print("WARNING: PS-CB-NA rated max ~3.6 V; you requested", args.voltage_mv, "mV", file=sys.stderr)

    ppk = open_ppk(args.port)
    try:
        ppk.set_source_voltage(args.voltage_mv)
        time.sleep(0.15)
        if args.action == "status-setup":
            print(f"OK source-meter {args.voltage_mv} mV on {args.port} (DUT state unchanged)")
        elif args.action == "on":
            ppk.toggle_DUT_power("ON")
            print(f"OK DUT ON @ {args.voltage_mv} mV ({args.port})")
        elif args.action == "off":
            ppk.toggle_DUT_power("OFF")
            print(f"OK DUT OFF ({args.port})")
        elif args.action == "cycle":
            ppk.toggle_DUT_power("OFF")
            print(f"DUT OFF ({args.off_seconds}s)...")
            time.sleep(args.off_seconds)
            ppk.toggle_DUT_power("ON")
            print(f"OK DUT ON @ {args.voltage_mv} mV ({args.port}) — power cycle complete")
        return 0
    finally:
        try:
            ppk.ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
