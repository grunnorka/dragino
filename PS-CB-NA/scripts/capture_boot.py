"""Capture-only boot log with timestamps. Listen first, prompt RESET while capturing.

Prints RAW bytes (printable-escaped) prefixed with elapsed seconds, so timing and
non-newline / wrong-baud output are all visible.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import dragino_uart as du  # noqa: E402

PORT = "/dev/ttyUSB0"
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
WATCH = len(sys.argv) > 2 and sys.argv[2] == "watch"


def main() -> None:
    ser = du.open_serial(PORT, 9600)
    ser.timeout = 0.2
    msg = (f"Capturing {SECS:.0f}s on {PORT}.\n\nDO NOT press RESET — just watch."
           if WATCH else
           f"Capturing {SECS:.0f}s on {PORT}.\n\nPress RESET on the board NOW, once.")
    subprocess.Popen(
        ["zenity", "--info", "--title", "PS-CB capture", "--text", msg],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    t0 = time.monotonic()
    deadline = t0 + SECS
    nbytes = 0
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            nbytes += len(chunk)
            txt = "".join(chr(b) if 32 <= b < 127 or b in (10, 13, 9) else f"\\x{b:02x}"
                          for b in chunk)
            print(f"[{time.monotonic()-t0:6.2f}] {txt}", end="", flush=True)
    print(f"\n--- {nbytes} bytes captured ---", flush=True)


if __name__ == "__main__":
    main()
