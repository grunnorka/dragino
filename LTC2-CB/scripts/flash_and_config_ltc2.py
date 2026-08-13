# LTC2-CB Firmware Flash + ThingsBoard MQTT Config
# Flash ONLY app image at 0x08007800 (preserve Dragino NB bootloader at 0x08000000).
# Prerequisites (hardware):
#   1) Disconnect sensor/baseboard GND jumpers per Dragino UART upgrade note
#   2) SW1 = ISP
#   3) USB-TTL on LTC2 UART
#   4) Press RESET once just before flash connect
# After flash: SW1 = Flash (normal), reconnect GND, unlock PIN 358613, apply MQTT.
# GND: disconnect DURING UART flash only; reconnect after upgrade for normal mode.

import argparse
import sys
import time
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "firmware" / "LTC2-CB_v1.1.0.bin"
APP_ADDR = 0x08007800
PIN = "358613"
TOKEN = "cdHsbYNjHJ7haAPkoJZD"
SERVADDR = "167.235.104.181,1883"  # IP preferred; hostname alt: vakt.systemat.is,1883

def list_ports():
    import serial.tools.list_ports
    for p in serial.tools.list_ports.comports():
        print(f"  {p.device}: {p.description} [{p.hwid}]")

def flash(port: str, baud: int = 115200):
    from stm32loader.main import main as stm32_main
    if not BIN.is_file():
        raise SystemExit(f"Missing firmware: {BIN}")
    size = BIN.stat().st_size
    # Region erase+write+verify app only - NEVER default 0x08000000 for this .bin
    args = [
        "--port", port,
        "--baud", str(baud),
        "--family", "L0",
        "--parity", "even",
        "--erase",
        "--write",
        "--verify",
        "--address", hex(APP_ADDR),
        "--length", str(size),
        "--verbose",
        str(BIN),
    ]
    print("STM32LOADER:", " ".join(args))
    print("IMPORTANT: SW1=ISP, press RESET now if not already, then flashing...")
    stm32_main(*args)

def at_session(port: str, baud: int = 9600):
    import serial
    ser = serial.Serial(port, baud, timeout=2)
    time.sleep(0.3)
    ser.reset_input_buffer()

    def send(cmd, wait=1.5):
        line = (cmd if cmd.endswith("\n") else cmd + "\n")
        print(f"TX: {cmd}")
        ser.write(line.encode("ascii", errors="ignore"))
        ser.flush()
        time.sleep(wait)
        rx = ser.read(ser.in_waiting or 1)
        t0 = time.time()
        chunks = [rx]
        while time.time() - t0 < wait:
            n = ser.in_waiting
            if n:
                chunks.append(ser.read(n))
            else:
                time.sleep(0.05)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        print(f"RX:\n{text}")
        return text

    send(PIN, 2)
    send("AT+CFG", 3)
    send("AT+MODEL=?", 2)
    for cmd, w in [
        ("AT+PRO=3,3", 2),
        (f"AT+SERVADDR={SERVADDR}", 2),
        (f"AT+UNAME={TOKEN}", 2),
        ("AT+PWD=NULL", 1.5),
        ("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5),
        ("AT+SUBTOPIC=v1/devices/me/attributes", 1.5),
        ("AT+MQOS=1", 1.5),
        ("AT+BKDNS=1,0", 2),
        ("AT+BKDNS=1,0,167.235.104.181,1883", 2),
        ("AT+CLOCKLOG=1,65535,5,8", 2),
        ("AT+TDC=1800", 2),
        ("AT+CFG", 4),
        ("AT+MODEL=?", 2),
        ("AT+SERVADDR=?", 1.5),
        ("AT+BKDNS=?", 1.5),
        ("AT+UNAME=?", 1.5),
        ("AT+PRO=?", 1.5),
        ("AT+TDC=?", 1.5),
        ("AT+CLOCKLOG=?", 1.5),
    ]:
        send(cmd, w)
    ser.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--port", help="LTC2 UART COM port")
    ap.add_argument("--flash", action="store_true")
    ap.add_argument("--config", action="store_true", help="Apply MQTT after SW1=Flash")
    ap.add_argument("--baud-isp", type=int, default=115200)
    ap.add_argument("--baud-at", type=int, default=9600)
    args = ap.parse_args()
    if args.list_ports:
        list_ports()
        return
    if not args.port:
        raise SystemExit("--port required")
    if args.flash:
        flash(args.port, args.baud_isp)
    if args.config:
        print("Ensure SW1=Flash/normal and device awake before AT config...")
        at_session(args.port, args.baud_at)

if __name__ == "__main__":
    main()