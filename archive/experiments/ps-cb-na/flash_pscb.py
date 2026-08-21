#!/usr/bin/env python3
"""Flash PS-CB_v1.2.0 app image only (preserve Dragino NB bootloader).

DEPRECATED: prefer scripts/recover_pscb.py, which writes the bootloader and the
current v1.2.1 app in one ISP session and prompts on screen for each hardware
step. See ../FIRMWARE_UPDATE.md.

Hardware:
  1) USB-TTL: GND↔GND, TX↔RX, RX↔TX
  2) SW1 = ISP
  3) Press RESET just before this script connects
After flash: SW1 = Flash (normal), RESET, then configure AT over 9600 8N1.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEX = ROOT / "PS-CB-NA" / "firmware" / "PS-CB_v1.2.0.hex"
BIN = ROOT / "PS-CB-NA" / "firmware" / "PS-CB_v1.2.0.bin"
APP_ADDR = 0x08007800
PAGE = 128  # STM32L0 flash page


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ensure_bin() -> Path:
    from intelhex import IntelHex

    if not HEX.is_file():
        raise SystemExit(f"Missing firmware: {HEX}")
    ih = IntelHex(str(HEX))
    if ih.minaddr() != APP_ADDR:
        raise SystemExit(
            f"Unexpected hex start {hex(ih.minaddr())}; expected {hex(APP_ADDR)}"
        )
    ih.tobinfile(str(BIN), start=APP_ADDR)
    data = bytearray(BIN.read_bytes())
    pad = (PAGE - (len(data) % PAGE)) % PAGE
    if pad:
        data.extend(b"\xff" * pad)
        BIN.write_bytes(data)
    return BIN


FLASH_BASE = 0x08000000


def flash(port: str, baud: int) -> None:
    from stm32loader.bootloader import Stm32Bootloader
    from stm32loader.uart import SerialConnection

    bin_path = ensure_bin()
    data = bin_path.read_bytes()
    size = len(data)
    if size % PAGE:
        raise SystemExit(f"bin length {size} is not a {PAGE}-byte page multiple")
    first_page = (APP_ADDR - FLASH_BASE) // PAGE
    page_count = size // PAGE
    last_page = first_page + page_count - 1
    if first_page < 1:
        raise SystemExit("refusing to erase bootloader region at flash base")
    pages = list(range(first_page, first_page + page_count))
    print(
        f"Flash app {bin_path.name} {size} bytes @ {hex(APP_ADDR)} "
        f"pages {first_page}-{last_page} (bootloader pages 0-{first_page - 1} kept)",
        flush=True,
    )
    print("IMPORTANT: SW1=ISP — press RESET during the countdown.", flush=True)
    for n in range(5, 0, -1):
        print(f"  RESET in {n}…", flush=True)
        time.sleep(1)

    conn = SerialConnection(port, baud, "E")
    conn.connect()
    stm32 = Stm32Bootloader(conn, device_family="L0", verbosity=5)
    stm32.SYNCHRONIZE_ATTEMPTS = 6
    print("Activating UART bootloader (DTR pulse + 0x7F)…", flush=True)
    stm32.reset_from_system_memory()
    conn.timeout = 60.0
    boot_ver = stm32.get()
    print(f"Bootloader version: {hex(boot_ver)}", flush=True)
    chip = stm32.get_id()
    print(f"Chip id: {hex(chip)}", flush=True)
    conn.timeout = 60.0
    batch = 128
    for i in range(0, len(pages), batch):
        chunk = pages[i : i + batch]
        print(f"Erase pages {chunk[0]}-{chunk[-1]} ({len(chunk)})...", flush=True)
        stm32.erase_memory(chunk)
    print("Write+verify in 4 KiB blocks...", flush=True)
    stm32.verbosity = 5
    block = 4096
    for off in range(0, size, block):
        piece = data[off : off + block]
        addr = APP_ADDR + off
        stm32.write_memory_data(addr, piece)
        read_back = stm32.read_memory_data(addr, len(piece))
        if read_back != piece:
            for i, (a, b) in enumerate(zip(piece, read_back)):
                if a != b:
                    raise SystemExit(
                        f"VERIFY_FAIL at {hex(addr + i)} wrote {a:02X} read {b:02X}"
                    )
        print(f"  OK {hex(addr)} +{len(piece)}", flush=True)
    print("Verification OK", flush=True)
    print("FLASH_OK — set SW1=Flash and press RESET", flush=True)


def main() -> None:
    load_env(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--port",
        default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"),
    )
    ap.add_argument("--baud", type=int, default=57600)
    args = ap.parse_args()
    if not os.access(args.port, os.R_OK | os.W_OK):
        print(
            f"ERROR: cannot open {args.port} (need dialout or: sudo chmod 666 {args.port})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    flash(args.port, args.baud)


if __name__ == "__main__":
    main()
