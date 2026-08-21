#!/usr/bin/env python3
"""Recover a PS-CB-NA: write the Dragino NB bootloader, then the app firmware.

Follows https://wiki.dragino.com/docs/NB-IoT/firmware-update/uart-ttl-upgrade-for-nb-iot-lte-m-devices/
  * bootloader .bin -> 0x08000000
  * app .hex        -> addresses taken from the hex itself (0x08007800)

Wiring: USB-TTL GND<->GND, TX<->RX, RX<->TX. SW1 in ISP for flashing,
back to Flash for normal operation. The script prompts on screen for
every switch/RESET action.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import prompt_user  # noqa: E402

FIRMWARE = ROOT / "PS-CB-NA" / "firmware"
BOOTLOADER_BIN = FIRMWARE / "DRAGINO_NB_bootloader_v1.3.bin"
APP_HEX = FIRMWARE / "PS-CB-NA_v1.2.1.hex"

FLASH_BASE = 0x08000000
APP_ADDR = 0x08007800
PAGE = 128  # STM32L0 flash page size
ERASE_BATCH = 128  # pages per extended-erase command
WRITE_BLOCK = 4096


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def pad_to_page(data: bytes) -> bytes:
    pad = (PAGE - (len(data) % PAGE)) % PAGE
    return data + b"\xff" * pad if pad else data


def app_image(path: Path | None = None) -> bytes:
    from intelhex import IntelHex

    hex_path = path or APP_HEX
    if not hex_path.is_file():
        raise SystemExit(f"Missing app firmware: {hex_path}")
    ih = IntelHex(str(hex_path))
    if ih.minaddr() != APP_ADDR:
        raise SystemExit(
            f"{hex_path.name} starts at {hex(ih.minaddr())}, expected {hex(APP_ADDR)}. "
            "Flashing it would overwrite the bootloader; refusing."
        )
    return pad_to_page(ih.tobinstr(start=APP_ADDR, end=ih.maxaddr()))


def bootloader_image() -> bytes:
    if not BOOTLOADER_BIN.is_file():
        raise SystemExit(f"Missing bootloader: {BOOTLOADER_BIN}")
    data = pad_to_page(BOOTLOADER_BIN.read_bytes())
    if FLASH_BASE + len(data) > APP_ADDR:
        raise SystemExit(
            f"Bootloader image is {len(data)} bytes and would reach into the app "
            f"region at {hex(APP_ADDR)}; refusing."
        )
    return data


def program(stm32, address: int, data: bytes, label: str) -> None:
    first_page = (address - FLASH_BASE) // PAGE
    pages = list(range(first_page, first_page + len(data) // PAGE))
    print(
        f"\n[{label}] {len(data)} bytes @ {hex(address)} "
        f"(pages {pages[0]}-{pages[-1]})",
        flush=True,
    )
    for i in range(0, len(pages), ERASE_BATCH):
        chunk = pages[i : i + ERASE_BATCH]
        print(f"  erase pages {chunk[0]}-{chunk[-1]}", flush=True)
        stm32.erase_memory(chunk)
    for offset in range(0, len(data), WRITE_BLOCK):
        piece = data[offset : offset + WRITE_BLOCK]
        addr = address + offset
        stm32.write_memory_data(addr, piece)
        read_back = bytes(stm32.read_memory_data(addr, len(piece)))
        if read_back != piece:
            bad = next(i for i, (a, b) in enumerate(zip(piece, read_back)) if a != b)
            raise SystemExit(
                f"  VERIFY FAILED at {hex(addr + bad)}: "
                f"wrote {piece[bad]:02X}, read {read_back[bad]:02X}"
            )
        done = offset + len(piece)
        print(
            f"  written+verified {done}/{len(data)} bytes "
            f"({100 * done // len(data)}%)",
            flush=True,
        )
    print(f"[{label}] OK", flush=True)


def describe_region(stm32, address: int, label: str) -> None:
    try:
        data = bytes(stm32.read_memory_data(address, 16))
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"  {label} @ {hex(address)}: unreadable ({exc})", flush=True)
        return
    # STM32L0 program memory reads back as 0x00 after erase, unlike the 0xFF of
    # most other STM32 families, so all-zero here means blank rather than "set".
    if data == b"\x00" * 16 or data == b"\xff" * 16:
        state = "ERASED (blank)"
    else:
        state = "programmed"
    stack = int.from_bytes(data[:4], "little")
    sane = 0x20000000 <= stack <= 0x20005000
    print(
        f"  {label} @ {hex(address)}: {state}, first bytes {data[:8].hex(' ')}, "
        f"initial SP {hex(stack)} ({'plausible' if sane else 'NOT a valid stack pointer'})",
        flush=True,
    )


def listen(port: str, seconds: float) -> bytes:
    import serial

    ser = serial.Serial(port, 9600, timeout=0.2)
    raw = bytearray()
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            chunk = ser.read(4096)
            if chunk:
                raw += chunk
                sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
    finally:
        ser.close()
    return bytes(raw)


def main() -> None:
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--skip-bootloader",
        action="store_true",
        help="only write the app image",
    )
    parser.add_argument(
        "--skip-app",
        action="store_true",
        help="only write the bootloader",
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=None,
        help="alternative app .hex (e.g. an openfw build); must still start at 0x08007800",
    )
    args = parser.parse_args()

    if not os.access(args.port, os.R_OK | os.W_OK):
        raise SystemExit(
            f"Cannot open {args.port}. Run: sudo chmod 666 {args.port}"
        )

    boot = bootloader_image()
    app = app_image(args.app)
    app_name = args.app.name if args.app else APP_HEX.name
    print(
        f"bootloader {BOOTLOADER_BIN.name}: {len(boot)} bytes -> {hex(FLASH_BASE)}\n"
        f"app        {app_name}: {len(app)} bytes -> {hex(APP_ADDR)}",
        flush=True,
    )

    prompt_user.step(
        "Put the board into ISP mode",
        [
            "1. Slide the SW1 jumper to the ISP position.",
            "2. Press and release the RESET button on the board.",
            "3. Then click the button below.",
            "",
            "The chip now waits silently in its ROM bootloader -",
            "no LED will light up, that is expected.",
        ],
        ok_label="SW1=ISP and RESET pressed",
    )

    from stm32loader.bootloader import Stm32Bootloader
    from stm32loader.uart import SerialConnection

    conn = SerialConnection(args.port, args.baud, "E")
    conn.connect()
    conn.timeout = 5.0
    stm32 = Stm32Bootloader(conn, device_family="L0", verbosity=0)
    stm32.SYNCHRONIZE_ATTEMPTS = 10
    print(f"Synchronising with ROM bootloader on {args.port} @ {args.baud} 8E1...", flush=True)
    stm32.reset_from_system_memory()
    conn.timeout = 60.0
    print(f"ROM bootloader version: {hex(stm32.get())}", flush=True)
    print(f"Chip id: {hex(stm32.get_id())}", flush=True)

    print("\nFlash contents before writing:", flush=True)
    describe_region(stm32, FLASH_BASE, "bootloader region")
    describe_region(stm32, APP_ADDR, "app region")

    if not args.skip_bootloader:
        program(stm32, FLASH_BASE, boot, "bootloader")
    if not args.skip_app:
        program(stm32, APP_ADDR, app, "app")
    conn.disconnect()

    prompt_user.step(
        "Back to normal (Flash) mode",
        [
            "1. Slide the SW1 jumper back to the Flash position.",
            "2. Click the button below.",
            "3. Then press RESET - the LED should blink and boot text appear.",
        ],
        ok_label="SW1=Flash, ready",
    )
    print("Listening on UART at 9600 8N1 for 20 s - press RESET now.\n", flush=True)
    raw = listen(args.port, 20.0)
    print(f"\n--- received {len(raw)} bytes ---", flush=True)
    if raw:
        print(raw.decode("utf-8", "replace")[:4000], flush=True)
        print("BOOT_OK", flush=True)
    else:
        print("SILENT - nothing came out of the UART.", flush=True)


if __name__ == "__main__":
    main()
