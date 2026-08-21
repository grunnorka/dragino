#!/usr/bin/env python3
"""Set daily upload interval (TDC=86400) and 4h sampling, 6 records per daily
uplink (CLOCKLOG=1,65535,240,6). The sampling field is 8-bit minutes (max 255),
so 6h/360min is impossible: stock wraps 360 to 104, openfw rejects it."""

import argparse
import os
import sys, time, serial
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'shared'))
from monitor import load_dotenv, resolve_pin

BAUD = 9600
TDC = 86400
CLOCKLOG = '1,65535,240,6'

def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default=os.environ.get('DRAGINO_PORT', '/dev/ttyUSB0'))
    args = parser.parse_args()

    load_dotenv(ROOT / '.env')
    pin = resolve_pin('')
    if not pin:
        print('No PIN', file=sys.stderr)
        return 2

    print(f'Port={args.port} TDC={TDC}s CLOCKLOG={CLOCKLOG}', flush=True)
    ser = serial.Serial(args.port, BAUD, timeout=0.25, write_timeout=2)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    buf = bytearray()

    def log(tag, text):
        safe = text.replace(pin, '***PIN***')
        row = f'{utc()} {tag} {safe}'
        print(row, flush=True)

    def drain(s):
        end = time.monotonic() + s
        out = []
        while time.monotonic() < end:
            try:
                chunk = ser.read(4096)
            except serial.SerialException:
                return None  # signal disconnect
            if chunk:
                buf.extend(chunk)
                while True:
                    i = buf.find(b'\n')
                    if i < 0:
                        break
                    t = bytes(buf[: i + 1]).decode('utf-8', errors='replace').rstrip('\r\n')
                    del buf[: i + 1]
                    if t:
                        log('RX', t)
                        out.append(t)
            else:
                time.sleep(0.02)
        return out

    def send(cmd, wait=2.0):
        shown = '***PIN***' if cmd.strip() in (pin, f'AT+PIN={pin}') else cmd
        log('TX', shown)
        ser.write((cmd.rstrip('\r\n') + '\r\n').encode('ascii', errors='ignore'))
        ser.flush()
        return drain(wait)

    def wait_for_idle(timeout=120):
        quiet = None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            lines = drain(1.0)
            if lines is None:
                return False  # disconnect
            for L in lines:
                if 'Upload start' in L or 'Searching for location' in L:
                    quiet = None
                if 'End of upload' in L or 'power-off successful' in L:
                    quiet = time.monotonic()
            if quiet and time.monotonic() - quiet > 3:
                return True
            if not lines and quiet is None:
                if not drain(3.0):
                    return True
        return False

    def unlock():
        for i in range(1, 40):
            got = send(pin, 2.0)
            if got is None:
                return False
            if any('Password Correct' in g for g in got):
                log('SYS', f'unlock_ok {i}')
                return True
            send(f'AT+PIN={pin}', 1.0)
            got = send('AT', 1.2)
            if got is None:
                return False
            if any(g.strip().upper() == 'OK' for g in got):
                if any('OK' in g for g in send('AT', 1.0)):
                    log('SYS', f'AT_OK {i}')
                    return True
        return False

    # Main loop with reconnect
    connected = False
    for attempt in range(10):
        try:
            if not ser.is_open:
                ser.open()
            print(f'Attempt {attempt+1}: waiting for idle...', flush=True)
            if not wait_for_idle(120):
                print('Not idle / disconnect, retrying...', flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)
                continue
            print('Idle. Unlocking...', flush=True)
            if not unlock():
                print('Unlock failed, retrying...', flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)
                continue
            connected = True
            print('Unlocked. Setting intervals...', flush=True)
            for cmd in [f'AT+CLOCKLOG={CLOCKLOG}', f'AT+TDC={TDC}']:
                send(cmd, 2.0)
            print('Verifying...', flush=True)
            for cmd in ['AT+CFG', 'AT+CLOCKLOG=?', 'AT+TDC=?']:
                send(cmd, 2.5)
            print('Rebooting with ATZ...', flush=True)
            send('ATZ', 2.0)
            print('Done.', flush=True)
            ser.close()
            return 0
        except serial.SerialException as e:
            print(f'Serial error: {e}, retrying...', flush=True)
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(1)

    print('Failed after retries', file=sys.stderr)
    return 2

if __name__ == '__main__':
    sys.exit(main())
