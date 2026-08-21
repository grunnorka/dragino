"""Reliable AT request/response over the Dragino UART.

The firmware prints asynchronous progress lines (``[12345]NB module ...``) while
AT commands are in flight, so a fixed read window mis-attributes replies to the
wrong command. These helpers ignore async chatter and wait for a real
``OK``/``ERROR`` terminator, and parse ``AT+CFG`` as one authoritative dump.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Optional

from dragino_uart import LineBuffer, read_for, send_line

RE_ASYNC = re.compile(r"^\[\d+\]")
RE_CFG_ENTRY = re.compile(r"^AT\+([A-Z0-9]+)=(.*)$")
TERMINATORS = ("OK", "ERROR")
# Printed by the firmware on its own schedule, never as a command reply.
ASYNC_PREFIXES = ("AT+PWRM", "AT+NAME")


def is_async(line: str) -> bool:
    text = line.strip()
    return bool(RE_ASYNC.match(text)) or text.startswith(ASYNC_PREFIXES)


def at_cmd(
    ser,
    cmd: str,
    buf: LineBuffer,
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 12.0,
) -> tuple[bool, list[str]]:
    """Send one AT command and collect its reply up to OK/ERROR.

    Returns (acked, payload_lines) where payload_lines excludes the
    terminator, the command echo, and asynchronous firmware chatter.
    """
    send_line(ser, cmd)
    payload: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        for line in read_for(ser, 0.3, buf, on_line):
            text = line.strip()
            if not text or is_async(text) or text == cmd:
                continue
            if text in TERMINATORS:
                return text == "OK", payload
            payload.append(text)
    return False, payload


def read_cfg(
    ser,
    buf: LineBuffer,
    on_line: Optional[Callable[[str], None]] = None,
    window: float = 20.0,
    settle: float = 3.0,
) -> dict[str, str]:
    """Run AT+CFG and parse the whole dump into {KEY: value}.

    AT+CFG emits dozens of ``AT+KEY=value`` lines without a single trailing
    terminator, so read until the output goes quiet for `settle` seconds.
    """
    send_line(ser, "AT+CFG")
    cfg: dict[str, str] = {}
    deadline = time.time() + window
    last_seen = time.time()
    while time.time() < deadline:
        lines = read_for(ser, 0.3, buf, on_line)
        for line in lines:
            text = line.strip()
            if is_async(text):
                continue
            match = RE_CFG_ENTRY.match(text)
            if match:
                cfg[match.group(1)] = match.group(2).strip()
                last_seen = time.time()
        if cfg and time.time() - last_seen > settle:
            break
    return cfg


def is_unset(value: Optional[str]) -> bool:
    return value is None or value.strip() == "" or value.strip().upper() == "NULL"


__all__ = ["at_cmd", "read_cfg", "is_unset", "is_async"]
