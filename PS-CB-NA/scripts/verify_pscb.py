#!/usr/bin/env python3
"""Verify a freshly flashed PS-CB-NA: boot banner, LED, AT unlock, config dump.

Run with SW1 in the Flash (normal) position. Prompts on screen for the
RESET press so the boot banner is captured from the first byte.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import prompt_user  # noqa: E402
from dragino_uart import (  # noqa: E402
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    send_line,
    unlock,
)

QUERIES = [
    "AT+CFG",
    "AT+PRO=?",
    "AT+SERVADDR=?",
    "AT+CLIENT=?",
    "AT+UNAME=?",
    "AT+PUBTOPIC=?",
    "AT+SUBTOPIC=?",
    "AT+TLSMOD=?",
    "AT+TDC=?",
    "AT+APN=?",
    "AT+CSQ",
]


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--boot-seconds", type=float, default=60.0)
    parser.add_argument("--no-config-dump", action="store_true")
    args = parser.parse_args()

    pin = resolve_pin()
    if not pin:
        raise SystemExit("No PIN found. Set DRAGINO_PIN in .env")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logpath = logdir / f"{stamp}_pscb_verify.log"
    log = logpath.open("w", encoding="utf-8")

    def out(text: str) -> None:
        print(text, flush=True)
        log.write(text + "\n")
        log.flush()

    prompt_user.step(
        "Reboot the sensor and watch the LED",
        [
            "SW1 must be in the Flash (normal) position.",
            "",
            "1. Click the button below.",
            "2. Then press RESET on the board.",
            "3. Watch the front LED - note whether it lights up.",
            "",
            f"The boot log will be captured for {int(args.boot_seconds)} seconds.",
        ],
        ok_label="Ready - I will press RESET now",
    )

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()
    out(f"=== boot capture on {args.port} 9600 8N1, {args.boot_seconds:.0f}s ===")
    boot_lines = read_for(ser, args.boot_seconds, buf, out)
    out(f"=== boot capture done: {len(boot_lines)} lines ===")

    blob = "\n".join(boot_lines)
    for label, needle in [
        ("bootloader banner", "bootloader"),
        ("image version", "Image Version"),
        ("model string", "PS-CB"),
        ("modem responded", "NBIOT has responded"),
    ]:
        out(f"  check {label}: {'FOUND' if needle.lower() in blob.lower() else 'absent'}")

    out("\n=== unlocking with PIN ===")
    result = unlock(ser, pin, policy="stable", timeout=180.0, on_line=out, on_tx=out)
    out(f"unlock ok={result.ok} phase={result.phase.value} hint={result.hint}")

    if result.ok and not args.no_config_dump:
        out("\n=== config dump ===")
        for cmd in QUERIES:
            out(f">>> {cmd}")
            send_line(ser, cmd)
            read_for(ser, 2.0, buf, out)

    ser.close()
    log.close()
    print(f"\nLog written to {logpath}", flush=True)
    if not result.ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
