#!/usr/bin/env python3
"""Quick A/B: point the openfw device at a public anonymous broker
(test.mosquitto.org, IP-only) and watch one uplink. Decides whether the
+QMTSTAT: 0,1 we see on the Railway proxy is our firmware's QMTCONN or the
proxy. No secrets involved (anonymous). Restores nothing — re-run
openfw_m4_verify.py afterwards to put the Railway config back.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du  # noqa: E402

ALT_IP = "54.36.178.49"   # test.mosquitto.org
ALT_PORT = 1883


def main() -> None:
    ser = du.open_serial("/dev/ttyUSB0", 9600)
    buf = du.LineBuffer()

    def rx(t: float) -> list[str]:
        lines = []
        for line in du.read_for(ser, t, buf, None):
            print(f"RX {line}", flush=True)
            lines.append(line)
        return lines

    def cmd(c: str, t: float = 6.0) -> None:
        print(f"TX {c}", flush=True)
        du.send_line(ser, c)
        rx(t)

    print("=== waiting for a live console (device may be mid-boot) ===", flush=True)
    rx(3.0)
    cmd("AT")  # gate is removed; expect OK

    print("=== point at test.mosquitto.org (anonymous, IP-only) ===", flush=True)
    cmd(f"AT+SERVADDR={ALT_IP},{ALT_PORT}")
    cmd(f"AT+BKDNS=1,0,{ALT_IP},{ALT_PORT}")
    cmd("AT+UNAME=")
    cmd("AT+PWD=")
    cmd("AT+CLIENT=pscb-ab")

    print("=== ATZ; watching for the uplink ===", flush=True)
    du.send_line(ser, "ATZ")
    deadline = time.monotonic() + 150.0
    while time.monotonic() < deadline:
        for line in du.read_for(ser, 1.0, buf, None):
            print(f"RX {line}", flush=True)
            if "Upload data successfully" in line or "BROKER" in line:
                print("=== RESULT: uplink SUCCEEDED on public broker ===", flush=True)
                ser.close()
                return
        # keep watching until deadline

    print("=== RESULT: no successful uplink within window ===", flush=True)
    ser.close()


if __name__ == "__main__":
    main()
