#!/usr/bin/env python3
"""Probe LTC2 on COM8: try bauds, wake with PIN/ACT."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import serial

PIN = "358613"
COM = "COM8"


def probe(baud: int, seconds: float = 8.0) -> None:
    print(f"\n=== PROBE {COM}@{baud} ===", flush=True)
    try:
        ser = serial.Serial(COM, baud, timeout=0.2)
    except Exception as exc:
        print(f"OPEN_FAIL {exc}", flush=True)
        return
    buf = b""
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        # burst PIN every ~1s
        if int(time.time() * 2) % 2 == 0 and n % 5 == 0:
            ser.write((PIN + "\r\n").encode("ascii"))
            ser.write(b"AT+MODEL=?\r\n")
            ser.flush()
            print(f"TX PIN+MODEL @{baud}", flush=True)
        n += 1
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").rstrip("\r")
                if text:
                    print(f"RX {text}", flush=True)
        else:
            time.sleep(0.05)
    ser.close()


def main() -> None:
    print(
        ">>> If no RX: press ACT 1-3s on LTC2 to wake UART <<<",
        flush=True,
    )
    for baud in (9600, 115200, 57600):
        probe(baud, 6.0)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
