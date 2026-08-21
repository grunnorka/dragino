#!/usr/bin/env python3
"""Capture a full openfw boot + modem bring-up log from the PS-CB-NA console.

Phase 1: prompt for RESET, record timestamped console output until the modem
         bring-up finishes (or a hard cap).
Phase 2: unlock the AT console and run query/config commands.
Phase 3 (--atz): send ATZ and capture the reboot too, then re-run the
         console queries (used to apply a persisted config change).

Usage: .venv/bin/python PS-CB-NA/scripts/openfw_boot_capture.py [--seconds 180]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du  # noqa: E402
import prompt_user  # noqa: E402

TERMINAL_MARKERS = (
    "Network time:",
    "Failed to get time",
    "Failed to activate PDP context",
    "Failed to configure parameters for TCP/IP context",
    "Signal Strength:99",
    "NBIOT did not respond",
)

t0 = time.monotonic()
logf = None


def log(direction: str, line: str) -> None:
    stamp = f"{time.monotonic() - t0:9.3f}"
    logf.write(f"[{stamp}] {direction} {line}\n")
    print(f"[{stamp}] {direction} {line}", flush=True)


def phase_capture(ser, buf, seconds: float) -> None:
    """Record until the modem bring-up ends. Terminal markers count only after
    this boot's [BOOT-A] — stale bytes from a previous boot (USB buffer) must
    not end the phase early. Nothing is transmitted here: the Dragino
    bootloader listens on USART2 right after reset and host traffic can hold
    it in its handshake loop and delay the app jump."""
    boot_seen = False
    seen_terminal = 0.0
    start = time.monotonic()
    while True:
        now = time.monotonic()
        if seen_terminal and now - seen_terminal > 8.0:
            break
        if now - start > seconds:
            break
        for line in du.read_for(ser, 0.5, buf, None):
            log("RX", line)
            if "[BOOT-A]" in line:
                boot_seen = True
            if boot_seen and not seen_terminal and \
                    any(m in line for m in TERMINAL_MARKERS):
                seen_terminal = now


def phase_console(ser, buf, pin: str, cmds) -> None:
    unlocked = False
    for _ in range(4):
        log("TX", "***PIN***")
        du.send_line(ser, pin)
        for line in du.read_for(ser, 1.8, buf, None):
            log("RX", line)
            if du.RE_PASSWORD_OK.search(line):
                unlocked = True
        if unlocked:
            break
        time.sleep(0.5)
    if not unlocked:
        log("--", "unlock failed, sending commands anyway")
    for cmd in cmds:
        log("TX", cmd)
        du.send_line(ser, cmd)
        for line in du.read_for(ser, 2.5, buf, None):
            log("RX", line)


def main() -> None:
    global logf
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    ap.add_argument("--seconds", type=float, default=180.0)
    ap.add_argument("--cmd", action="append", default=[],
                    help="extra console command for phase 2 (repeatable)")
    ap.add_argument("--atz", action="store_true",
                    help="after phase 2, ATZ and capture the reboot + queries")
    ap.add_argument("--powercycle", action="store_true",
                    help="ask for a full board power-cycle instead of RESET "
                         "(cold-starts the BG95 modem too)")
    args = ap.parse_args()

    du.load_dotenv(ROOT / ".env")
    pin = du.resolve_pin(device="ps-cb")

    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = ROOT / "logs" / f"{ts}_openfw_m3_boot.log"
    logf = open(log_path, "w", buffering=1, encoding="utf-8", errors="replace")

    ser = du.open_serial(args.port, 9600)
    if args.powercycle:
        prompt_user.step(
            "Power-cycle the board (cold modem start)",
            [
                "1. Make sure SW1 is in the Flash position.",
                "2. Remove ALL power from the board (USB/PSU and battery).",
                "3. Wait 3 seconds, then reconnect power.",
                "4. The board boots on its own — recording is already running.",
            ],
            ok_label="Power removed — reconnect now",
        )
    else:
        prompt_user.step(
            "Capture openfw boot",
            [
                "1. Make sure SW1 is in the Flash position.",
                "2. Click the button below, then press RESET on the board.",
                "3. Recording starts immediately.",
            ],
            ok_label="RESET now — record",
        )

    print(f"recording to {log_path}", flush=True)
    buf = du.LineBuffer()
    queries = ["AT+CSQ=?", "AT+DEUI=?"]

    phase_capture(ser, buf, args.seconds)
    phase_console(ser, buf, pin, queries + ["AT+CFG"] + args.cmd)

    if args.atz:
        log("TX", "ATZ")
        du.send_line(ser, "ATZ")
        phase_capture(ser, buf, args.seconds)
        phase_console(ser, buf, pin, queries)

    ser.close()
    logf.close()
    print(f"\nsaved: {log_path}", flush=True)


if __name__ == "__main__":
    main()
