"""Interactive AT console test against the running openfw app on /dev/ttyUSB0.

Sequence (per success criteria):
  AT          -> OK
  <PIN>       -> Password Correct  (DRAGINO_PIN from .env)
  AT+CFG      -> AT+KEY=value lines + OK
  AT+MODEL=?  -> PS-CB,openfw-... + OK
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import dragino_uart as du  # noqa: E402

PORT = "/dev/ttyUSB0"


def xact(ser, buf, cmd, wait=1.5):
    """Send a line, collect response lines."""
    if cmd is not None:
        du.send_line(ser, cmd)
    return du.read_for(ser, wait, buf)


def main() -> None:
    pin = os.environ.get("DRAGINO_PIN", "").strip()
    if not pin:
        # Load .env from repo root if present
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.is_file():
            for line in env_path.read_text().splitlines():
                if line.startswith("DRAGINO_PIN="):
                    pin = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not pin:
        raise SystemExit("Set DRAGINO_PIN in the environment or repo-root .env")

    ser = du.open_serial(PORT, 9600)
    buf = du.LineBuffer()

    print("=== listen 3s (expect heartbeat if app alive) ===")
    for L in xact(ser, buf, None, 3.0):
        print("  RX:", L)

    steps = [
        ("AT", "OK"),
        (pin, "Password Correct"),
        ("AT+CFG", "AT+"),
        ("AT+MODEL=?", "PS-CB"),
    ]
    for cmd, expect in steps:
        shown = "<PIN>" if cmd == pin else cmd
        print(f"\n=== TX: {shown}  (expect {expect!r}) ===")
        lines = xact(ser, buf, cmd, 2.0 if cmd == "AT+CFG" else 1.5)
        for L in lines:
            print("  RX:", L)
        ok = any(expect in L for L in lines)
        print(f"  -> {'PASS' if ok else 'FAIL'}")

    print("\n=== console latched UART (from a heartbeat) ===")
    for L in xact(ser, buf, None, 6.0):
        print("  RX:", L)


if __name__ == "__main__":
    main()
