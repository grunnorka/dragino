#!/usr/bin/env python3
"""Restore the known-good Railway config (TDC=180) after diagnostics, and dump
AT+CFG to confirm. Secrets masked."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import dragino_uart as du  # noqa: E402
from railway_mqtt import load_config  # noqa: E402


def main() -> None:
    cfg = load_config()
    secrets = [cfg["MQTT_PASS"], cfg["MQTT_USER"]]
    host, port = cfg["MQTT_FALLBACK_IP"], cfg["MQTT_PORT"]

    def mask(s: str) -> str:
        for sec in secrets:
            if sec:
                s = s.replace(sec, "***")
        return s

    ser = du.open_serial("/dev/ttyUSB0", 9600)
    buf = du.LineBuffer()

    def cmd(c: str, t: float = 6.0) -> None:
        print(f"TX {mask(c)}", flush=True)
        du.send_line(ser, c)
        for line in du.read_for(ser, t, buf, None):
            print(f"RX {mask(line)}", flush=True)

    for line in du.read_for(ser, 2.0, buf, None):
        print(f"RX {mask(line)}", flush=True)
    cmd("AT")
    for c in (
        "AT+PRO=3,5",
        f"AT+SERVADDR={host},{port}",
        f"AT+BKDNS=1,0,{host},{port}",
        "AT+CLIENT=ps-cb",
        f"AT+UNAME={cfg['MQTT_USER']}",
        f"AT+PWD={cfg['MQTT_PASS']}",
        "AT+PUBTOPIC=dragino/ps-cb/up",
        "AT+SUBTOPIC=dragino/ps-cb/down",
        "AT+TLSMOD=0,0",
        "AT+MQOS=1",
        "AT+DEBUG=1",
        "AT+TDC=180",
    ):
        cmd(c)
    cmd("AT+CFG", t=8.0)
    ser.close()


if __name__ == "__main__":
    main()
