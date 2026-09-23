#!/usr/bin/env python3
"""Flash the PS-CB-NA application image behind the Dragino NB bootloader (app region only).

Preserves the bootloader at 0x08000000-0x080077FF. Only erases/writes app pages
at/above 0x08007800. Accepts an Intel HEX file and uses the STM32 ROM bootloader
(ISP mode) via stm32loader.

CLI contract (used by the firmware Makefile `make flash` target):
    recover_pscb.py --skip-bootloader --app <absolute-path-to-hex>
        [--port /dev/ttyUSB0] [--baud 115200]

--soft-isp (requires an app already flashed with the AT+ISP console command,
ps-cb-openfw app/console.c): skips both physical steps. Instead of the
"SW1=ISP + press RESET" prompt, sends AT+ISP over the console UART -- the app
jumps itself into the same ROM ISP bootloader. Instead of the closing
"SW1=Flash + press RESET" prompt, sends the ROM bootloader's own GO command
back to 0x08000000 -- no reset line, no switch. UNTESTED ON HARDWARE as of
this writing; the SW1/RESET path above is unaffected and remains the
fallback if AT+ISP entry doesn't land.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import serial
from intelhex import IntelHex
from stm32loader.bootloader import CommandError, DataMismatchError, Stm32Bootloader
from stm32loader.main import Stm32Loader

APP_ADDR = 0x08007800
BOOTLOADER_TOP = 0x080077FF
BOOTLOADER_BASE = 0x08000000
PAGE_SIZE = 128
CHUNK_PAGES = 32  # 4 KiB per chunk
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 115200
CONSOLE_BAUD = 9600  # app console UART, docs/PINMAP.md
MAX_SYNC_ATTEMPTS = 5
CHUNK_RETRIES = 3


def enter_isp_via_serial(port: str, baud: int = CONSOLE_BAUD) -> bool:
    """Send AT+ISP on the running app's console UART so it jumps itself into
    the ROM ISP bootloader. Returns True if the command was written (not
    proof the jump landed -- the caller still syncs via open_loader())."""
    try:
        with serial.Serial(port, baud, timeout=1.5) as ser:
            ser.reset_input_buffer()
            ser.write(b"AT+ISP\r\n")
            ser.flush()
            resp = ser.read(64)
            print(f"AT+ISP -> {resp!r}", flush=True)
    except Exception as e:
        print(f"AT+ISP send failed ({e}); falling back to SW1/RESET prompt", flush=True)
        return False
    time.sleep(0.5)  # let the reset + jump land before we try to sync
    return True


def zenity_popup(text: str) -> bool:
    """Show a blocking zenity question; return True if user clicked OK/Done."""
    try:
        result = subprocess.run(
            [
                "zenity",
                "--question",
                "--title=PS-CB bench",
                f"--text={text}",
                "--ok-label=Done",
                "--cancel-label=Abort",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def load_hex(hex_path: Path) -> tuple[bytes, int]:
    """Load the Intel HEX and return (binary data, base address).

    Raises if the image does not start exactly at APP_ADDR or contains data
    below APP_ADDR.
    """
    if not hex_path.is_file():
        raise SystemExit(f"Missing hex file: {hex_path}")
    ih = IntelHex(str(hex_path))
    segments = ih.segments()
    if not segments:
        raise SystemExit("Empty hex file (no segments)")

    min_addr = min(seg[0] for seg in segments)
    max_addr = max(seg[1] - 1 for seg in segments)

    if min_addr != APP_ADDR:
        raise SystemExit(
            f"Refusing to flash: hex starts at 0x{min_addr:08X}, "
            f"must be exactly 0x{APP_ADDR:08X}"
        )
    if any(seg[0] < APP_ADDR for seg in segments):
        raise SystemExit(
            f"Refusing to flash: hex contains data below 0x{APP_ADDR:08X}"
        )

    size = max_addr - min_addr + 1
    data = ih.tobinarray(start=min_addr, size=size).tobytes()
    return data, min_addr


def open_loader(port: str, baud: int) -> Stm32Loader:
    loader = Stm32Loader()
    loader.parse_arguments(
        [
            "--port",
            port,
            "--baud",
            str(baud),
            "--family",
            "L0",
            "--parity",
            "even",
            "--no-progress",
        ]
    )
    loader.connect()
    try:
        loader.stm32.connection.timeout = 5
    except Exception:
        pass
    return loader


def close_loader(loader: Stm32Loader | None) -> None:
    if not loader:
        return
    try:
        loader.stm32.connection.serial_connection.close()
    except Exception:
        pass


def flash_image(port: str, baud: int, data: bytes, address: int, soft_isp: bool = False) -> None:
    if address % PAGE_SIZE or address < APP_ADDR:
        raise SystemExit(f"Bad flash address 0x{address:08X}")

    flash_base = 0x08000000
    n_pages = (len(data) + PAGE_SIZE - 1) // PAGE_SIZE
    first = (address - flash_base) // PAGE_SIZE

    print(
        f"Flash {len(data)} bytes @ 0x{address:08X} (pages {first}..{first + n_pages - 1})",
        flush=True,
    )

    loader: Stm32Loader | None = None
    sync_ok = False
    for sync_attempt in range(1, MAX_SYNC_ATTEMPTS + 1):
        try:
            close_loader(loader)
            loader = open_loader(port, baud)
            loader.read_device_id()
            loader.read_device_uid()
            sync_ok = True
            break
        except Exception as e:
            print(f"Bootloader sync attempt {sync_attempt}/{MAX_SYNC_ATTEMPTS} failed: {e}", flush=True)
            close_loader(loader)
            loader = None
            if sync_attempt < MAX_SYNC_ATTEMPTS:
                if soft_isp:
                    time.sleep(0.5)
                elif not zenity_popup(
                    "ISP sync failed.\nSW1 is already on ISP.\nPress RESET on the PS-CB board, then click Done."
                ):
                    raise SystemExit("Aborted by user")
                else:
                    time.sleep(0.5)

    if not sync_ok or loader is None:
        raise SystemExit("Could not establish bootloader sync")

    offset = 0
    page_i = first
    while offset < len(data):
        n = min(CHUNK_PAGES, first + n_pages - page_i)
        chunk = data[offset : offset + n * PAGE_SIZE]
        pad_len = n * PAGE_SIZE - len(chunk)
        if pad_len:
            chunk = chunk + b"\xff" * pad_len
        pages_chunk = list(range(page_i, page_i + n))
        chunk_addr = address + offset
        print(
            f"Chunk pages {pages_chunk[0]}..{pages_chunk[-1]} "
            f"addr 0x{chunk_addr:08X} len {len(chunk)}",
            flush=True,
        )

        ok = False
        last_err: Exception | None = None
        for attempt in range(1, CHUNK_RETRIES + 1):
            try:
                loader.stm32.erase_memory(pages_chunk)
                loader.stm32.write_memory_data(chunk_addr, chunk)
                read_back = loader.stm32.read_memory_data(chunk_addr, len(chunk))
                Stm32Bootloader.verify_data(read_back, chunk)
                ok = True
                break
            except (CommandError, DataMismatchError, OSError) as e:
                last_err = e
                print(f"  retry {attempt}/{CHUNK_RETRIES} after {e}", flush=True)
                close_loader(loader)
                time.sleep(0.4)
                if not soft_isp and not zenity_popup(
                    "Flash chunk failed.\nSW1 is already on ISP.\nPress RESET on the PS-CB board, then click Done."
                ):
                    raise SystemExit("Aborted by user")
                loader = open_loader(port, baud)
        if not ok:
            raise SystemExit(f"Chunk failed at 0x{chunk_addr:08X}: {last_err}")

        offset += n * PAGE_SIZE
        page_i += n

    print("Verification OK (all chunks)", flush=True)
    if soft_isp:
        # Native ROM-bootloader GO command: jump straight back into the
        # Dragino bootloader's reset vector, no NRST/BOOT0 pin involved.
        # SW1 must already be at "Flash" (never touched by this path) for
        # that bootloader to then jump on into the app as normal.
        loader.stm32.go(BOOTLOADER_BASE)
    else:
        try:
            loader.stm32.reset_from_flash()  # no-op: no RTS/DTR reset wiring on this rig
        except Exception:
            pass
    close_loader(loader)
    print("FLASH_OK", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Flash PS-CB-NA application image while preserving the Dragino bootloader."
    )
    ap.add_argument("--skip-bootloader", action="store_true", required=True)
    ap.add_argument("--app", type=Path, required=True)
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument(
        "--soft-isp",
        action="store_true",
        help="Enter/exit ISP mode over serial (AT+ISP + ROM 'GO') instead of "
        "SW1/RESET. Needs an app already flashed with AT+ISP support. "
        "Untested on hardware -- falls back to the SW1/RESET prompt if the "
        "AT+ISP send itself fails.",
    )
    args = ap.parse_args()

    data, address = load_hex(args.app)

    soft_isp = args.soft_isp
    if soft_isp:
        print("Soft-ISP: sending AT+ISP instead of prompting for SW1/RESET", flush=True)
        soft_isp = enter_isp_via_serial(args.port)

    if not soft_isp and not zenity_popup(
        "SW1 is already on ISP.\nPress RESET on the PS-CB board to enter the bootloader, then click Done."
    ):
        print("Aborted by user", flush=True)
        return 1

    flash_image(args.port, args.baud, data, address, soft_isp=soft_isp)

    if soft_isp:
        print("FLASH_OK, back in the app -- no SW1/RESET needed.", flush=True)
        return 0

    if not zenity_popup(
        "Flash successful.\nSet SW1 to Flash (normal), press RESET on the PS-CB board, then click Done."
    ):
        print("User did not confirm SW1=Flash; device may still be in ISP mode", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
