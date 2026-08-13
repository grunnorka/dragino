"""Shared Dragino UART open + unlock state machine.

Hard rules:
  - unlocked only after \"Password Correct\"
  - no PIN TX while BOOTLOADER or UPLOADING
  - MODEM_FAIL / sustained BOOTLOADER → stop with actionable hint (exit code 2)

Policies:
  quiet  — sparse PIN; aggressive only in modem-init window (LTC2 default)
  burst  — on app banner, short PIN blast (LTC2 race)
  stable — wait idle then PIN (PS-CB)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional, Sequence

try:
    import serial
    from serial.tools import list_ports
except ImportError as e:  # pragma: no cover
    raise SystemExit("pyserial required: pip install -r requirements.txt") from e

ROOT = Path(__file__).resolve().parent.parent

# Exit codes for agents / CLIs
EXIT_OK = 0
EXIT_UNLOCK_BLOCKED = 2  # modem fail / bootloader / no Password Correct
EXIT_RADIO_FAIL = 3
EXIT_BROKER_FAIL = 4
EXIT_USAGE = 5

HINT_MODEM_FAIL = (
    "NBIOT did not respond — reseat SIM, check antenna, confirm APN "
    "(e.g. lpwa.vodafone.is). Wait for 'NBIOT has responded' / Echo mode, "
    "then ACT 1–3s. Or use BLE config for LTC2."
)
HINT_BOOTLOADER = (
    "NB bootloader loop — set SW1=Flash (not ISP), power-cycle (JP2), "
    "ACT 1–3s only (not long-press OTA)."
)


class UartPhase(str, Enum):
    IDLE = "idle"
    BOOTLOADER = "bootloader"
    MODEM_INIT = "modem_init"
    MODEM_FAIL = "modem_fail"
    READY = "ready"
    UNLOCKED = "unlocked"
    UPLOADING = "uploading"


RE_PASSWORD_OK = re.compile(r"Password\s+Correct", re.I)
RE_PASSWORD_PROMPT = re.compile(r"password|AT\+PIN|please\s+input|enter\s+pass", re.I)
RE_BOOTLOADER = re.compile(r"bootloader", re.I)
RE_MODEM_FAIL = re.compile(r"NBIOT\s+did\s+not\s+respond", re.I)
RE_MODEM_OK = re.compile(r"NBIOT\s+has\s+responded", re.I)
RE_MODEM_INIT = re.compile(r"NB\s+module\s+is\s+initializing|NB\s+module", re.I)
RE_ECHO = re.compile(r"Echo\s+mode", re.I)
RE_RDY = re.compile(r"^RDY\s*$", re.I)
RE_APP_BANNER = re.compile(
    r"SensorManual|LTC2\s+sensor\s+Detected|AT\+NAME|Image\s+Version|DRAGINO\s+LTC2|"
    r"DRAGINO\s+PS-?CB|PS-CB",
    re.I,
)
RE_UPLOAD_START = re.compile(r"Upload\s+start|Start\s+of\s+upload", re.I)
RE_UPLOAD_END = re.compile(r"End\s+of\s+upload|power-off\s+successful", re.I)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def load_local_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_pin(cli_pin: str = "", *, device: str = "") -> str:
    """CLI pin wins; else device-specific env; else DRAGINO_PIN; else LTC2 sensorInfo."""
    if cli_pin:
        return cli_pin.strip()
    device = (device or "").lower().replace("_", "-")
    if device in ("ltc2", "ltc2-cb"):
        for key in ("DRAGINO_PIN_LTC2", "LTC2_PIN"):
            v = os.environ.get(key, "").strip()
            if v:
                return v
        info = ROOT / "LTC2-CB" / "sensorInfo.txt"
        if info.is_file():
            for raw in info.read_text(encoding="utf-8", errors="replace").splitlines():
                if "PIN" in raw.upper() and ":" in raw:
                    _, _, val = raw.partition(":")
                    val = val.strip()
                    if val.isdigit() and len(val) >= 4:
                        return val
    for key in ("DRAGINO_PIN", "PIN"):
        env_val = os.environ.get(key, "").strip()
        if env_val:
            return env_val
    cfg = load_local_config(ROOT / "config.local.json")
    for key in ("pin", "DRAGINO_PIN", "PIN"):
        val = cfg.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def list_com_ports() -> List[str]:
    return [f"{p.device}: {p.description}" for p in list_ports.comports()]


def open_serial(
    port: str,
    baud: int = 9600,
    *,
    timeout: float = 0.2,
) -> "serial.Serial":
    """Open UART with DTR/RTS held low (avoid FTDI reset glitches)."""
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = timeout
    ser.write_timeout = 2
    ser.bytesize = serial.EIGHTBITS
    ser.parity = serial.PARITY_NONE
    ser.stopbits = serial.STOPBITS_ONE
    ser.xonxoff = False
    ser.rtscts = False
    ser.dsrdtr = False
    ser.dtr = False
    ser.rts = False
    ser.open()
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:
        pass
    try:
        ser.set_buffer_size(rx_size=256 * 1024, tx_size=16 * 1024)
    except Exception:
        pass
    time.sleep(0.15)
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    return ser


@dataclass
class UnlockResult:
    ok: bool
    phase: UartPhase
    hint: str = ""
    exit_code: int = EXIT_OK
    lines: List[str] = field(default_factory=list)
    password_correct: bool = False
    model_ok: Optional[bool] = None


class LineBuffer:
    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> List[str]:
        if not chunk:
            return []
        self._buf.extend(chunk)
        out: List[str] = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            raw = bytes(self._buf[:i])
            del self._buf[: i + 1]
            text = raw.decode("utf-8", errors="replace").rstrip("\r")
            if text:
                out.append(text)
        return out


def classify_line(line: str) -> Optional[UartPhase]:
    if RE_BOOTLOADER.search(line):
        return UartPhase.BOOTLOADER
    if RE_MODEM_FAIL.search(line):
        return UartPhase.MODEM_FAIL
    if RE_UPLOAD_START.search(line):
        return UartPhase.UPLOADING
    if RE_UPLOAD_END.search(line):
        return UartPhase.READY
    if RE_PASSWORD_OK.search(line):
        return UartPhase.UNLOCKED
    if RE_MODEM_OK.search(line) or RE_ECHO.search(line) or RE_RDY.search(line):
        return UartPhase.READY
    if RE_MODEM_INIT.search(line):
        return UartPhase.MODEM_INIT
    if RE_APP_BANNER.search(line) or RE_PASSWORD_PROMPT.search(line):
        return UartPhase.READY
    return None


def send_line(ser: "serial.Serial", cmd: str) -> None:
    ser.write((cmd.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
    ser.flush()


def read_for(
    ser: "serial.Serial",
    seconds: float,
    buf: LineBuffer,
    on_line: Optional[Callable[[str], None]] = None,
) -> List[str]:
    end = time.monotonic() + seconds
    out: List[str] = []
    while time.monotonic() < end:
        chunk = ser.read(4096)
        lines = buf.feed(chunk)
        for L in lines:
            if on_line:
                on_line(L)
            out.append(L)
        if not chunk:
            time.sleep(0.02)
    return out


def _update_phase(phase: UartPhase, line: str) -> UartPhase:
    nxt = classify_line(line)
    if nxt is None:
        return phase
    if phase == UartPhase.UNLOCKED and nxt != UartPhase.BOOTLOADER:
        return phase
    if nxt == UartPhase.UPLOADING:
        return UartPhase.UPLOADING
    if phase == UartPhase.UPLOADING and nxt == UartPhase.READY:
        return UartPhase.READY
    if nxt == UartPhase.MODEM_FAIL:
        return UartPhase.MODEM_FAIL
    if nxt == UartPhase.BOOTLOADER:
        return UartPhase.BOOTLOADER
    if nxt == UartPhase.UNLOCKED:
        return UartPhase.UNLOCKED
    if nxt == UartPhase.MODEM_INIT and phase != UartPhase.UNLOCKED:
        return UartPhase.MODEM_INIT
    if nxt == UartPhase.READY and phase in (
        UartPhase.IDLE,
        UartPhase.BOOTLOADER,
        UartPhase.MODEM_INIT,
        UartPhase.MODEM_FAIL,
        UartPhase.READY,
    ):
        return UartPhase.READY
    return phase


def unlock(
    ser: "serial.Serial",
    pin: str,
    *,
    policy: str = "quiet",
    timeout: float = 240.0,
    confirm_model: Optional[str] = None,
    on_line: Optional[Callable[[str], None]] = None,
    on_tx: Optional[Callable[[str], None]] = None,
) -> UnlockResult:
    """Run unlock until Password Correct or blocked/timeout.

    confirm_model: optional prefix e.g. \"LTC2-CB\" — query AT+MODEL=? after unlock.
    """
    if not pin:
        return UnlockResult(
            ok=False,
            phase=UartPhase.IDLE,
            hint="Missing PIN (DRAGINO_PIN / device pin)",
            exit_code=EXIT_USAGE,
        )

    policy = (policy or "quiet").lower().strip()
    if policy not in ("quiet", "burst", "stable"):
        return UnlockResult(
            ok=False,
            phase=UartPhase.IDLE,
            hint=f"Unknown unlock policy: {policy}",
            exit_code=EXIT_USAGE,
        )

    buf = LineBuffer()
    phase = UartPhase.IDLE
    deadline = time.monotonic() + timeout
    last_pin = 0.0
    bootloader_hits = 0
    modem_fail_hits = 0
    all_lines: List[str] = []
    saw_app = False

    def emit_rx(L: str) -> None:
        nonlocal phase, bootloader_hits, modem_fail_hits, saw_app
        all_lines.append(L)
        if on_line:
            on_line(L)
        phase = _update_phase(phase, L)
        if RE_BOOTLOADER.search(L):
            bootloader_hits += 1
        if RE_MODEM_FAIL.search(L):
            modem_fail_hits += 1
        if RE_APP_BANNER.search(L):
            saw_app = True
            bootloader_hits = 0
        if RE_MODEM_OK.search(L) or RE_MODEM_INIT.search(L):
            bootloader_hits = 0

    def tx(cmd: str) -> List[str]:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        if on_tx:
            on_tx(shown)
        send_line(ser, cmd)
        return read_for(ser, 0.4 if policy == "burst" else 1.2, buf, emit_rx)

    def pin_allowed() -> bool:
        return phase not in (UartPhase.BOOTLOADER, UartPhase.UPLOADING, UartPhase.UNLOCKED)

    def finish_ok() -> UnlockResult:
        model_ok: Optional[bool] = None
        if confirm_model:
            got = tx("AT+MODEL=?")
            model_ok = any(
                L.strip().upper().startswith(confirm_model.upper()) for L in got
            )
        return UnlockResult(
            ok=True,
            phase=UartPhase.UNLOCKED,
            password_correct=True,
            model_ok=model_ok,
            lines=all_lines[-50:],
            exit_code=EXIT_OK,
        )

    # --- burst: wait for app banner then PIN blast ---
    if policy == "burst":
        windows = 6
        for _ in range(windows):
            if time.monotonic() >= deadline:
                break
            window_end = min(deadline, time.monotonic() + 90.0)
            saw_app = False
            while time.monotonic() < window_end:
                lines = read_for(ser, 0.5, buf, emit_rx)
                if modem_fail_hits >= 2 and bootloader_hits >= 1:
                    return UnlockResult(
                        ok=False,
                        phase=UartPhase.MODEM_FAIL,
                        hint=HINT_MODEM_FAIL,
                        exit_code=EXIT_UNLOCK_BLOCKED,
                        lines=all_lines[-30:],
                    )
                if any(RE_PASSWORD_OK.search(L) for L in lines):
                    phase = UartPhase.UNLOCKED
                    return finish_ok()
                if saw_app and pin_allowed():
                    break
            if not saw_app or not pin_allowed():
                if bootloader_hits >= 3 and modem_fail_hits >= 1:
                    return UnlockResult(
                        ok=False,
                        phase=UartPhase.BOOTLOADER,
                        hint=HINT_BOOTLOADER + " " + HINT_MODEM_FAIL,
                        exit_code=EXIT_UNLOCK_BLOCKED,
                        lines=all_lines[-30:],
                    )
                continue
            for _blast in range(12):
                if not pin_allowed():
                    break
                got = tx(pin)
                if any(RE_PASSWORD_OK.search(L) for L in got):
                    phase = UartPhase.UNLOCKED
                    return finish_ok()
            got = tx(f"AT+PIN={pin}")
            if any(RE_PASSWORD_OK.search(L) for L in got):
                phase = UartPhase.UNLOCKED
                return finish_ok()
        hint = HINT_MODEM_FAIL if modem_fail_hits else HINT_BOOTLOADER
        return UnlockResult(
            ok=False,
            phase=phase,
            hint=hint,
            exit_code=EXIT_UNLOCK_BLOCKED,
            lines=all_lines[-30:],
        )

    # --- stable: optional idle wait then periodic PIN ---
    if policy == "stable":
        quiet_since: Optional[float] = None
        idle_deadline = min(deadline, time.monotonic() + 90.0)
        while time.monotonic() < idle_deadline:
            lines = read_for(ser, 1.0, buf, emit_rx)
            for L in lines:
                if RE_UPLOAD_START.search(L):
                    quiet_since = None
                if RE_UPLOAD_END.search(L):
                    quiet_since = time.monotonic()
            if quiet_since and time.monotonic() - quiet_since > 5:
                break
            if not lines and quiet_since is None:
                more = read_for(ser, 3.0, buf, emit_rx)
                if not more:
                    break

        while time.monotonic() < deadline:
            if modem_fail_hits >= 2:
                return UnlockResult(
                    ok=False,
                    phase=UartPhase.MODEM_FAIL,
                    hint=HINT_MODEM_FAIL,
                    exit_code=EXIT_UNLOCK_BLOCKED,
                    lines=all_lines[-30:],
                )
            if bootloader_hits >= 4:
                return UnlockResult(
                    ok=False,
                    phase=UartPhase.BOOTLOADER,
                    hint=HINT_BOOTLOADER,
                    exit_code=EXIT_UNLOCK_BLOCKED,
                    lines=all_lines[-30:],
                )
            if pin_allowed():
                got = tx(pin)
                if any(RE_PASSWORD_OK.search(L) for L in got):
                    phase = UartPhase.UNLOCKED
                    return finish_ok()
                got = tx(f"AT+PIN={pin}")
                extra = read_for(ser, 1.0, buf, emit_rx)
                if any(RE_PASSWORD_OK.search(L) for L in got + extra):
                    phase = UartPhase.UNLOCKED
                    return finish_ok()
                # PS-CB sometimes already unlocked — require Password Correct still;
                # do not treat bare AT OK as unlock.
            else:
                read_for(ser, 0.8, buf, emit_rx)
            time.sleep(0.3)
        return UnlockResult(
            ok=False,
            phase=phase,
            hint="Timeout waiting for Password Correct (press ACT 1–3s)",
            exit_code=EXIT_UNLOCK_BLOCKED,
            lines=all_lines[-30:],
        )

    # --- quiet (default LTC2) ---
    while time.monotonic() < deadline:
        lines = read_for(ser, 0.9, buf, emit_rx)
        if any(RE_PASSWORD_OK.search(L) for L in lines):
            phase = UartPhase.UNLOCKED
            return finish_ok()

        if modem_fail_hits >= 2 and bootloader_hits >= 1:
            return UnlockResult(
                ok=False,
                phase=UartPhase.MODEM_FAIL,
                hint=HINT_MODEM_FAIL,
                exit_code=EXIT_UNLOCK_BLOCKED,
                lines=all_lines[-30:],
            )
        if bootloader_hits >= 5 and modem_fail_hits == 0 and phase == UartPhase.BOOTLOADER:
            return UnlockResult(
                ok=False,
                phase=UartPhase.BOOTLOADER,
                hint=HINT_BOOTLOADER,
                exit_code=EXIT_UNLOCK_BLOCKED,
                lines=all_lines[-30:],
            )

        if not pin_allowed():
            continue

        modem_window = phase == UartPhase.MODEM_INIT or any(
            RE_MODEM_INIT.search(L) or RE_ECHO.search(L) or RE_MODEM_OK.search(L)
            for L in lines
        )
        wake = any(
            RE_RDY.search(L)
            or "Signal Strength" in L
            or RE_ECHO.search(L)
            or RE_MODEM_OK.search(L)
            or RE_MODEM_INIT.search(L)
            or RE_PASSWORD_PROMPT.search(L)
            or RE_APP_BANNER.search(L)
            for L in lines
        )
        now = time.monotonic()
        interval = 0.8 if modem_window else 8.0
        pin_due = (wake or modem_window) and (now - last_pin >= interval)
        if not pin_due and now - last_pin >= 8.0 and bootloader_hits == 0:
            pin_due = True
        if pin_due and bootloader_hits == 0:
            last_pin = now
            if on_tx:
                on_tx("***PIN***")
            send_line(ser, pin)
            got = read_for(ser, 1.5 if modem_window else 1.8, buf, emit_rx)
            if any(RE_PASSWORD_OK.search(L) for L in got):
                phase = UartPhase.UNLOCKED
                return finish_ok()

    hint = HINT_MODEM_FAIL if modem_fail_hits else (
        HINT_BOOTLOADER if bootloader_hits else "Timeout waiting for Password Correct"
    )
    return UnlockResult(
        ok=False,
        phase=phase,
        hint=hint,
        exit_code=EXIT_UNLOCK_BLOCKED,
        lines=all_lines[-30:],
    )


def wait_idle(ser: "serial.Serial", seconds: float = 90.0, on_line: Optional[Callable[[str], None]] = None) -> None:
    buf = LineBuffer()
    quiet_since: Optional[float] = None
    end = time.monotonic() + seconds

    def emit(L: str) -> None:
        if on_line:
            on_line(L)

    while time.monotonic() < end:
        lines = read_for(ser, 1.0, buf, emit)
        for L in lines:
            if RE_UPLOAD_START.search(L):
                quiet_since = None
            if RE_UPLOAD_END.search(L):
                quiet_since = time.monotonic()
        if quiet_since and time.monotonic() - quiet_since > 5:
            return
        if not lines and quiet_since is None:
            if not read_for(ser, 3.0, buf, emit):
                return


__all__ = [
    "EXIT_OK",
    "EXIT_UNLOCK_BLOCKED",
    "EXIT_RADIO_FAIL",
    "EXIT_BROKER_FAIL",
    "EXIT_USAGE",
    "HINT_MODEM_FAIL",
    "HINT_BOOTLOADER",
    "UartPhase",
    "UnlockResult",
    "LineBuffer",
    "load_dotenv",
    "resolve_pin",
    "list_com_ports",
    "open_serial",
    "classify_line",
    "send_line",
    "read_for",
    "unlock",
    "wait_idle",
    "RE_PASSWORD_OK",
    "RE_BOOTLOADER",
    "RE_MODEM_FAIL",
]
