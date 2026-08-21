#!/usr/bin/env python3
"""Probe a PS-CB-NA without the unlock loop: send AT commands directly.

Waits for the app banner after a RESET, then sends AT / AT+CFG without any
PIN. If the firmware does not answer (or answers Password Incorrect), sends
a fallback PIN once (default 000000) and, on Password Correct, re-sends the
commands.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import prompt_user  # noqa: E402
from dragino_uart import (  # noqa: E402
    RE_APP_BANNER,
    RE_PASSWORD_OK,
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    send_line,
)

COMMANDS = ["AT", "AT+CFG"]
# real answers, not echoes of what we sent: OK/ERROR terminators or AT+CFG
# dump lines of the form AT+NAME=value
RE_ANSWER = re.compile(r"^(OK|ERROR|AT\+\w+=)")


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    ap.add_argument("--fallback-pin", default="000000")
    ap.add_argument("--wait-banner", type=float, default=90.0)
    args = ap.parse_args()

    prompt_user.step(
        "Reboot the sensor",
        [
            "SW1 must be in the Flash (normal) position.",
            "",
            "1. Click the button below.",
            "2. Then press RESET on the board.",
        ],
        ok_label="Ready - I will press RESET now",
    )

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()

    def out(text: str) -> None:
        print(text, flush=True)

    print(f"--- waiting up to {args.wait_banner:.0f}s for app banner ---", flush=True)
    deadline = time.monotonic() + args.wait_banner
    saw_banner = False
    while time.monotonic() < deadline:
        lines = read_for(ser, 1.0, buf, out)
        if any(RE_APP_BANNER.search(L) for L in lines):
            saw_banner = True
            break
    if not saw_banner:
        print("WARN: no app banner seen, trying anyway", flush=True)
    time.sleep(1.0)

    def send_commands() -> list[str]:
        got: list[str] = []
        for cmd in COMMANDS:
            print(f">>> {cmd}", flush=True)
            send_line(ser, cmd)
            got += read_for(ser, 3.0, buf, out)
        return got

    got = send_commands()
    answered = any(RE_ANSWER.match(L.strip()) for L in got)
    if answered and not any("Password Incorrect" in L for L in got):
        print("--- direct commands answered, no PIN needed ---", flush=True)
        ser.close()
        return

    print(
        f"\n--- no usable answer, sending fallback PIN {args.fallback_pin} once ---",
        flush=True,
    )
    send_line(ser, args.fallback_pin)
    got = read_for(ser, 3.0, buf, out)
    if not any(RE_PASSWORD_OK.search(L) for L in got):
        send_line(ser, f"AT+PIN={args.fallback_pin}")
        got += read_for(ser, 3.0, buf, out)
    if any(RE_PASSWORD_OK.search(L) for L in got):
        print("--- Password Correct, re-sending commands ---", flush=True)
        send_commands()
    else:
        print("--- fallback PIN rejected too ---", flush=True)
        read_for(ser, 2.0, buf, out)
        ser.close()
        raise SystemExit(2)
    ser.close()


if __name__ == "__main__":
    main()
