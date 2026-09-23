#!/usr/bin/env python3
"""PS-CB-NA bench CLI: PPK2 source-meter power + RTS/ISP flash (agent-friendly).

Requires:
  - Nordic PPK2 on USB (source-meter / internal supply) powering the board
  - FTDI USB-TTL on the console/ISP UART, with RTS wired to the Flash/ISP
    (BOOT0) switch: RTS=False -> ISP, RTS=True -> Flash (empirically confirmed)

Bench facts (measured 2026-09-23, see PSCB_PPK2_CLI.md):
  - BOOT0 follows RTS. A *closed* UART port releases RTS, which is the ISP
    level, so the board only boots the app while this CLI holds the console
    port open with RTS at the Flash level. Every command that powers the DUT
    therefore holds the console (unless --no-uart / --boot-mode isp).
  - Closing the PPK2 port drops DTR, which resets the PPK2 (USB re-enumerates,
    ~1 s) and cuts DUT power (LED back to green). Every invocation therefore
    cold-boots the board. --hold-seconds keeps it up while the CLI runs;
    --keep-power closes with DTR left up so the DUT stays powered after exit.
  - Dragino bootloader v1.3 probes the modem for ~27 s before it starts the
    app; the openfw banner arrives ~28 s after power-on.

Exit codes:
  0  success
  1  operational failure (ISP sync, flash, AT probe, ...)
  2  usage / missing deps / bad args
  3  hardware missing (no PPK2 / UART)

Examples (from dragino repo root, with .venv):

  .venv/bin/python shared/pscb_ppk2_cli.py flash-run \\
      --hex ../ps-cb-openfw/build/pscb-openfw.hex \\
      --result logs/flash-result.json \\
      --current-log logs/ppk2-current.jsonl

  .venv/bin/python shared/pscb_ppk2_cli.py boot
  .venv/bin/python shared/pscb_ppk2_cli.py monitor --seconds 120
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import termios
except ImportError:  # Windows: HUPCL control unavailable
    termios = None

# Repo paths
_HERE = Path(__file__).resolve().parent
_DRAGINO_ROOT = _HERE.parent
_SCRIPTS = _DRAGINO_ROOT / "PS-CB-NA" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    import serial
    from ppk2_api.ppk2_api import PPK2_API, PPK2_Command
except ImportError as e:
    print(f"Missing dependency: {e}\n  pip install ppk2-api pyserial", file=sys.stderr)
    raise SystemExit(2) from e

DEFAULT_UART = os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0")
DEFAULT_MV = 3600          # PS-CB-NA rating is ~2.6-3.6 V; BG95 VBAT min is 3.3 V
RATED_MAX_MV = 3600
DEFAULT_OFF_S = 3.0
DEFAULT_BOOT_TIMEOUT_S = 45.0   # bootloader modem probe alone takes ~27 s
CONSOLE_BAUD = 9600
ISP_BAUD = 115200
PPK_SAMPLE_RATE = 100_000       # PPK2 streams 100 kS/s
LOG_WINDOW_S = 0.05             # one JSONL record per window
# Empirically: RTS asserted (True) = Flash/normal; deasserted (False) = ISP
DEFAULT_ISP_RTS = False

# The PPK2 answers GET_META_DATA only once per USB session, so after a
# --keep-power exit (no reset) calibration must come from this cache.
MODIFIER_CACHE_DIR = Path.home() / ".cache" / "pscb-ppk2"

BOOTLOADER_MARKER = "DRAGINO NB bootloader"
# Any of these means the application (openfw or stock) is running
APP_MARKERS = ("[BOOT-A]", "SensorManual", "Image Version:")


# UART handles to hold in break (TX low) while the DUT is unpowered. A board
# asleep in STOP draws so little that the FTDI's idle-high TX line feeds it
# through the MCU RX pin's protection diode and it never resets.
_QUIET_UARTS: list = []


def register_uart(ser) -> None:
    if ser not in _QUIET_UARTS:
        _QUIET_UARTS.append(ser)


def unregister_uart(ser) -> None:
    if ser in _QUIET_UARTS:
        _QUIET_UARTS.remove(ser)


def _uart_break(on: bool) -> None:
    for ser in list(_QUIET_UARTS):
        try:
            ser.break_condition = on
        except Exception:
            pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _discover_ppk(explicit: Optional[str], wait_s: float = 6.0) -> str:
    """Find the PPK2. Waits out the ~1 s USB re-enumeration the PPK2 does
    whenever a previous process closed its port with DTR dropped."""
    deadline = time.time() + wait_s
    while True:
        if explicit and explicit != "auto":
            if Path(explicit).exists() or explicit.upper().startswith("COM"):
                return explicit
        else:
            devs = PPK2_API.list_devices()
            if devs:
                return devs[0]
        if time.time() >= deadline:
            raise FileNotFoundError(
                f"PPK2 port not found: {explicit}" if explicit and explicit != "auto"
                else "No PPK2 found (VID 1915:C00A)"
            )
        time.sleep(0.25)


def _set_hupcl(ser: "serial.Serial", hangup: bool) -> None:
    """HUPCL decides whether closing the port drops DTR/RTS.

    PPK2: a DTR drop resets it (USB re-enumerates) and cuts DUT power.
    FTDI: dropping RTS moves BOOT0 to the ISP level. The flag persists on the
    tty across opens, so every close sets it explicitly.
    """
    if termios is None:
        return
    try:
        fd = ser.fileno()
        attrs = termios.tcgetattr(fd)
        if hangup:
            attrs[2] |= termios.HUPCL
        else:
            attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""


@dataclass
class RunResult:
    ok: bool
    command: str
    started_at: str
    finished_at: str = ""
    voltage_mv: int = DEFAULT_MV
    ppk_port: str = ""
    uart: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_log: Optional[str] = None
    console_log: Optional[str] = None
    current_summary: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def add(self, step: StepResult) -> None:
        self.steps.append(asdict(step))
        if not step.ok:
            self.ok = False
            if not self.error:
                self.error = step.error or f"step {step.name} failed"

    def write(self, path: Optional[Path]) -> None:
        self.finished_at = _utc_now()
        payload = asdict(self)
        text = json.dumps(payload, indent=2, sort_keys=True)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
        # Always emit a one-line summary for agents scraping stdout
        print("RESULT_JSON " + json.dumps(payload, sort_keys=True), flush=True)


class _PhaseStats:
    __slots__ = ("n", "sum", "min", "max", "t_first", "t_last")

    def __init__(self) -> None:
        self.n = 0
        self.sum = 0.0
        self.min = float("inf")
        self.max = float("-inf")
        self.t_first = 0.0
        self.t_last = 0.0

    def add(self, samples: list[float], now: float) -> None:
        if not self.n:
            self.t_first = now
        self.t_last = now
        self.n += len(samples)
        self.sum += sum(samples)
        self.min = min(self.min, min(samples))
        self.max = max(self.max, max(samples))


class CurrentSampler:
    """Sole reader of the PPK2 stream while armed.

    Reads in a tight loop so the full 100 kS/s is captured (the old
    read-4 KiB-then-sleep loop kept ~20% and lagged ~0.5 s). Aggregates
    LOG_WINDOW_S windows into JSONL and keeps per-phase totals for the
    result summary. Windows never straddle a phase change.
    """

    def __init__(self, ppk: PPK2_API, path: Optional[Path], voltage_mv: int, phase: str = "armed"):
        self.ppk = ppk
        self.path = path
        self.voltage_mv = voltage_mv
        self._phase = phase
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats: dict[str, _PhaseStats] = {}
        self._recent: list[tuple[float, float, int]] = []  # (ts, sum_uA, n) per window
        self._fh = None
        self.t_start = 0.0
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "w", encoding="utf-8")

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def start(self) -> None:
        self.t_start = time.time()
        self._thread = threading.Thread(target=self._run, name="ppk2-current", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._fh:
            self._fh.close()
            self._fh = None

    def total_samples(self) -> int:
        with self._lock:
            return sum(st.n for st in self._stats.values()) + len(self._recent)

    def recent_avg(self, seconds: float = 0.5) -> Optional[float]:
        cutoff = time.time() - seconds
        with self._lock:
            rows = [r for r in self._recent if r[0] >= cutoff]
        n = sum(r[2] for r in rows)
        return sum(r[1] for r in rows) / n if n else None

    def summary(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._stats.items())
        v = self.voltage_mv / 1000.0
        phases: dict[str, Any] = {}
        tot_n = 0
        tot_sum = 0.0
        for name, st in items:
            charge_uC = st.sum / PPK_SAMPLE_RATE  # uA * (1/fs) s
            phases[name] = {
                "avg_uA": round(st.sum / st.n, 2),
                "min_uA": round(st.min, 2),
                "max_uA": round(st.max, 2),
                "samples": st.n,
                "seconds": round(st.n / PPK_SAMPLE_RATE, 3),
                "charge_uC": round(charge_uC, 1),
                "energy_mJ": round(charge_uC * v / 1000.0, 3),
            }
            tot_n += st.n
            tot_sum += st.sum
        wall = (time.time() - self.t_start) if self.t_start else 0.0
        charge_uC = tot_sum / PPK_SAMPLE_RATE
        return {
            "voltage_mv": self.voltage_mv,
            "capture_rate_sps": round(tot_n / wall) if wall else 0,
            "total": {
                "avg_uA": round(tot_sum / tot_n, 2) if tot_n else None,
                "samples": tot_n,
                "seconds": round(tot_n / PPK_SAMPLE_RATE, 3),
                "charge_uC": round(charge_uC, 1),
                "energy_mJ": round(charge_uC * (self.voltage_mv / 1000.0) / 1000.0, 3),
            },
            "phases": phases,
        }

    def _emit(self, phase: str, samples: list[float], now: float) -> None:
        s = sum(samples)
        with self._lock:
            st = self._stats.setdefault(phase, _PhaseStats())
            st.add(samples, now)
            self._recent.append((now, s, len(samples)))
            if len(self._recent) > 400:  # ~20 s of windows
                del self._recent[:100]
        if self._fh:
            rec = {
                "ts": round(now, 3),
                "t": round(now - self.t_start, 3),
                "uA": round(s / len(samples), 3),
                "n": len(samples),
                "min_uA": round(min(samples), 3),
                "max_uA": round(max(samples), 3),
                "phase": phase,
            }
            self._fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def _run(self) -> None:
        ser = self.ppk.ser
        window: list[float] = []
        window_phase = self._phase
        window_start = time.time()
        while not self._stop.is_set():
            try:
                raw = ser.read(ser.in_waiting or 1)
                now = time.time()
                with self._lock:
                    phase = self._phase
                if window and (phase != window_phase or now - window_start >= LOG_WINDOW_S):
                    self._emit(window_phase, window, now)
                    window = []
                if not window:
                    window_phase = phase
                    window_start = now
                if raw:
                    samples, _digital = self.ppk.get_samples(raw)
                    if samples:
                        window.extend(samples)
            except Exception as e:
                if self._fh:
                    self._fh.write(json.dumps({"ts": time.time(), "error": str(e), "phase": window_phase}) + "\n")
                time.sleep(0.2)
        if window:
            self._emit(window_phase, window, time.time())


class PpkSession:
    """Owns the PPK2 serial link for the whole CLI invocation."""

    def __init__(self, port: str, voltage_mv: int):
        self.port = port
        self.voltage_mv = voltage_mv
        self.ppk = PPK2_API(port, timeout=1, write_timeout=1)
        self.ppk.ser.timeout = 0.2  # bounds how long the sampler takes to stop
        self.serial_number = self._serial_number(port)
        self._wr = threading.Lock()  # command writes; the sampler only reads
        self.modifiers_source = ""
        self.sampler: Optional[CurrentSampler] = None

    def close(self, keep_power: bool = False) -> None:
        """keep_power=True leaves DTR up on close so the PPK2 does not reset
        and the DUT stays powered after this process exits."""
        if self.sampler:
            self.sampler.stop()
        with self._wr:
            try:
                self.ppk._write_serial((PPK2_Command.AVERAGE_STOP,))
                time.sleep(0.05)
            except Exception:
                pass
            _set_hupcl(self.ppk.ser, hangup=not keep_power)
            try:
                self.ppk.ser.close()
            except Exception:
                pass

    def summary(self) -> Optional[dict[str, Any]]:
        if not self.sampler:
            return None
        out = self.sampler.summary()
        out["modifiers"] = self.modifiers_source or "missing"
        out["ppk_serial"] = self.serial_number
        return out

    def _drain(self, seconds: float = 0.8) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                n = self.ppk.ser.in_waiting
                if n:
                    self.ppk.ser.read(n)
            except Exception:
                break
            time.sleep(0.05)

    def _soft_stop(self) -> None:
        """Stop measuring / DUT without USB RESET (RESET re-enumerates the ACM)."""
        try:
            self.ppk._write_serial((PPK2_Command.AVERAGE_STOP,))
            time.sleep(0.1)
            self.ppk._write_serial((PPK2_Command.DEVICE_RUNNING_SET, PPK2_Command.NO_OP))
            time.sleep(0.1)
            self._drain(0.6)
        except Exception:
            pass

    @staticmethod
    def _serial_number(port: str) -> str:
        try:
            import serial.tools.list_ports

            for p in serial.tools.list_ports.comports():
                if p.device == port and p.serial_number:
                    return p.serial_number
        except Exception:
            pass
        return "unknown"

    def _load_modifiers(self) -> str:
        """Returns 'device', 'cache' or '' (not loaded)."""
        cache = MODIFIER_CACHE_DIR / f"modifiers-{self.serial_number}.txt"
        for _ in range(2):
            try:
                self.ppk._write_serial((PPK2_Command.GET_META_DATA,))
                time.sleep(0.25)
                chunks: list[bytes] = []
                for __ in range(10):
                    b = self.ppk.ser.read(self.ppk.ser.in_waiting or 1)
                    if b:
                        chunks.append(b)
                    time.sleep(0.08)
                text = b"".join(chunks).decode("utf-8", errors="ignore")
                if "END" in text:
                    self.ppk._parse_metadata(text)
                    if self.serial_number != "unknown":
                        try:
                            cache.parent.mkdir(parents=True, exist_ok=True)
                            cache.write_text(text, encoding="utf-8")
                        except OSError:
                            pass
                    return "device"
            except Exception:
                time.sleep(0.2)
        if cache.is_file():
            try:
                self.ppk._parse_metadata(cache.read_text(encoding="utf-8"))
                return "cache"
            except Exception:
                pass
        return ""

    def arm(self, current_log: Optional[Path] = None, dut_on: bool = True) -> Optional[float]:
        """Source-meter mode, set voltage, start measuring + sampler.

        dut_on=False leaves the DUT unpowered (callers that power-cycle next
        do not need an extra boot). Returns the spot average in uA when on.

        A process started right after another one closed the PPK2 can open
        the old ACM node just before the PPK2 resets and re-enumerates; every
        later write then goes nowhere. So the sample stream is checked and,
        if silent, the port is rediscovered and the arm redone once.
        """
        for attempt in (1, 2):
            avg = self._arm_once(current_log, dut_on)
            deadline = time.time() + 1.5
            while time.time() < deadline and self.sampler and self.sampler.total_samples() == 0:
                time.sleep(0.1)
            if self.sampler and self.sampler.total_samples() > 0:
                return avg
            if attempt == 2:
                raise RuntimeError("PPK2 is not streaming samples after reopen")
            print("PPK2 not streaming (stale handle during re-enumeration?); reopening", flush=True)
            if self.sampler:
                self.sampler.stop()
                self.sampler = None
            try:
                self.ppk.ser.close()
            except Exception:
                pass
            time.sleep(2.0)
            self.port = _discover_ppk("auto")
            self.ppk = PPK2_API(self.port, timeout=1, write_timeout=1)
            self.ppk.ser.timeout = 0.2
            self.serial_number = self._serial_number(self.port)
        return None

    def _arm_once(self, current_log: Optional[Path], dut_on: bool) -> Optional[float]:
        self._soft_stop()
        self.modifiers_source = self._load_modifiers()
        if not self.modifiers_source:
            print("WARNING: PPK2 modifiers not loaded; current values may be off", flush=True)
        if self.voltage_mv > RATED_MAX_MV:
            print(
                f"WARNING: PS-CB-NA rated max ~3.6 V; using {self.voltage_mv} mV",
                flush=True,
            )
        with self._wr:
            self.ppk.set_source_voltage(self.voltage_mv)
            time.sleep(0.2)
            self.ppk.use_source_meter()
            time.sleep(0.3)
            if dut_on:
                self.ppk.toggle_DUT_power("ON")
                time.sleep(0.2)
            self.ppk.start_measuring()

        self.sampler = CurrentSampler(
            self.ppk, current_log, self.voltage_mv, phase="armed_on" if dut_on else "armed_off"
        )
        self.sampler.start()
        time.sleep(0.5)
        avg = self.sampler.recent_avg(0.4) if dut_on else None
        print(
            f"PPK2 armed: source-meter @ {self.voltage_mv} mV on {self.port}, DUT {'ON' if dut_on else 'OFF'}"
            + (f" avg_uA={avg:.1f}" if avg is not None else ""),
            flush=True,
        )
        return avg

    def set_phase(self, phase: str) -> None:
        if self.sampler:
            self.sampler.set_phase(phase)

    def dut_on(self, phase: str = "dut_on") -> None:
        with self._wr:
            self.ppk.toggle_DUT_power("ON")
        self.set_phase(phase)
        print(f"DUT ON @ {self.voltage_mv} mV", flush=True)

    def dut_off(self, phase: str = "dut_off") -> None:
        with self._wr:
            self.ppk.toggle_DUT_power("OFF")
        self.set_phase(phase)
        print("DUT OFF", flush=True)

    def cycle(self, off_seconds: float = DEFAULT_OFF_S, phase: str = "cycle") -> float:
        """DUT off, wait, on. Returns the host time of power-on.

        Registered UARTs are held in break (TX low) while off so the FTDI
        cannot phantom-power a sleeping board; the break's start bit also
        wakes it from STOP so its supply collapses quickly.
        """
        self.set_phase(f"{phase}_off")
        _uart_break(True)
        with self._wr:
            self.ppk.toggle_DUT_power("OFF")
        print(f"DUT OFF ({off_seconds}s)...", flush=True)
        time.sleep(off_seconds)
        _uart_break(False)
        with self._wr:
            self.ppk.toggle_DUT_power("ON")
        t_on = time.time()
        self.set_phase(f"{phase}_on")
        print(f"DUT ON @ {self.voltage_mv} mV", flush=True)
        return t_on


class ConsoleHold:
    """Holds the console UART open with RTS at a fixed BOOT0 level.

    Opening the port with RTS preset avoids a level glitch on open. A reader
    thread timestamps every console line (relative to mark_power_on()) and
    optionally mirrors them to a log file.
    """

    def __init__(self, uart: str, rts: bool, log_path: Optional[Path] = None):
        self.uart = uart
        self.rts = rts
        s = serial.Serial()
        s.port = uart
        s.baudrate = CONSOLE_BAUD
        s.timeout = 0.1
        s.rts = rts
        s.dtr = False
        s.open()
        self.ser = s
        register_uart(s)
        self._lock = threading.Lock()
        self._lines: list[tuple[float, str]] = []
        self._partial = b""
        self._t0 = time.time()
        self._stop = threading.Event()
        self._fh = open(log_path, "a", encoding="utf-8") if log_path else None
        self._thread = threading.Thread(target=self._run, name="console", daemon=True)
        self._thread.start()

    def mark_power_on(self, t_on: Optional[float] = None) -> None:
        with self._lock:
            self._t0 = t_on or time.time()
            self._lines = []
        self._log(f"---- power on ({_utc_now()}) ----")

    def _log(self, text: str) -> None:
        if self._fh:
            self._fh.write(text + "\n")
            self._fh.flush()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception:
                break
            if not chunk:
                continue
            now = time.time()
            data = self._partial + chunk
            parts = data.split(b"\n")
            self._partial = parts.pop()
            with self._lock:
                for p in parts:
                    line = p.decode("latin1", errors="replace").rstrip("\r")
                    t = now - self._t0
                    self._lines.append((t, line))
                    self._log(f"{t:8.3f}  {line}")

    def lines_since(self, idx: int) -> list[tuple[float, str]]:
        with self._lock:
            return self._lines[idx:]

    def line_count(self) -> int:
        with self._lock:
            return len(self._lines)

    def wait_for(self, markers: tuple[str, ...], timeout: float, start_idx: int = 0) -> Optional[tuple[float, str]]:
        deadline = time.time() + timeout
        idx = start_idx
        while time.time() < deadline:
            for t, line in self.lines_since(idx):
                idx += 1
                if any(m in line for m in markers):
                    return t, line
            time.sleep(0.05)
        return None

    def command(self, cmd: str, timeout: float = 2.0) -> tuple[bool, list[str]]:
        """Send an AT line; collect lines until OK/ERROR or timeout."""
        idx = self.line_count()
        self._log(f">>> {cmd}")
        self.ser.write(cmd.encode("ascii") + b"\r\n")
        self.ser.flush()
        deadline = time.time() + timeout
        got: list[str] = []
        while time.time() < deadline:
            for _t, line in self.lines_since(idx):
                idx += 1
                got.append(line)
                if line.strip() == "OK":
                    return True, got
                if "ERROR" in line:
                    return False, got
            time.sleep(0.05)
        return False, got

    def transcript(self, max_chars: int = 3000) -> str:
        with self._lock:
            text = "\n".join(f"{t:7.2f} {line}" for t, line in self._lines)
        return text[-max_chars:]

    def close(self, keep_rts: bool = False) -> None:
        """keep_rts=True leaves RTS at its level after close (e.g. Flash, so a
        later reset of a still-powered DUT boots the app, not ROM ISP)."""
        self._stop.set()
        self._thread.join(timeout=1.0)
        unregister_uart(self.ser)
        _set_hupcl(self.ser, hangup=not keep_rts)
        try:
            self.ser.close()
        except Exception:
            pass
        if self._fh:
            self._fh.close()
            self._fh = None


def _hold_rts(uart: str, rts: bool, baud: int = ISP_BAUD) -> serial.Serial:
    ser = serial.Serial(uart, baud, timeout=0.2)
    ser.dtr = False
    ser.rts = rts
    time.sleep(0.05)
    ser.dtr = False
    ser.rts = rts
    return ser


def isp_sync(uart: str, isp_rts: bool, ppk: PpkSession, off_seconds: float) -> bool:
    """Hold RTS, power-cycle into ROM ISP, sync 0x7F. Returns True on ACK."""
    from stm32loader.bootloader import Stm32Bootloader
    from stm32loader.uart import SerialConnection

    print(f"ISP entry: RTS={isp_rts}", flush=True)
    conn = SerialConnection(uart, ISP_BAUD, "E")
    conn.connect()
    try:
        conn.serial_connection.dtr = False
        conn.serial_connection.rts = isp_rts
        time.sleep(0.05)
        conn.serial_connection.rts = isp_rts
        register_uart(conn.serial_connection)
        try:
            ppk.cycle(off_seconds, phase="isp")
        finally:
            unregister_uart(conn.serial_connection)
        time.sleep(0.8)
        conn.serial_connection.rts = isp_rts
        conn.flush_imput_buffer()
        for attempt in range(1, 8):
            conn.serial_connection.reset_input_buffer()
            conn.write(bytes([0x7F]))
            got = conn.read(1)
            print(f"  sync {attempt}: {got!r}", flush=True)
            if got == bytes([Stm32Bootloader.Reply.ACK]):
                print("  ISP SYNC OK", flush=True)
                return True
            time.sleep(0.25)
        return False
    finally:
        conn.disconnect()


def do_flash(uart: str, hex_path: Path, isp_rts: bool, ppk: PpkSession, off_seconds: float) -> dict[str, Any]:
    from pscb_app_isp import flash_image, load_hex

    # load_hex/flash_image raise SystemExit on refusal/failure -- surface as errors
    try:
        data, addr = load_hex(hex_path)
    except SystemExit as e:
        raise RuntimeError(f"load_hex: {e}") from None

    # Fresh ISP entry for the write
    hold = _hold_rts(uart, isp_rts, ISP_BAUD)
    register_uart(hold)
    try:
        ppk.cycle(off_seconds, phase="flash_isp")
        time.sleep(0.8)
        hold.rts = isp_rts
    finally:
        unregister_uart(hold)
        hold.close()

    print(f"Flash {len(data)} bytes @ 0x{addr:08X}", flush=True)
    ppk.set_phase("flash_write")
    t0 = time.time()
    # soft_isp=True: no zenity; board already in ROM ISP from power-cycle
    try:
        flash_image(uart, ISP_BAUD, data, addr, soft_isp=True)
    except SystemExit as e:
        raise RuntimeError(f"flash_image: {e}") from None
    return {"bytes": len(data), "address": f"0x{addr:08X}", "write_s": round(time.time() - t0, 1)}


def boot_and_probe(
    console: ConsoleHold,
    ppk: PpkSession,
    off_seconds: float,
    boot_timeout: float,
    probe_at: bool = True,
) -> dict[str, Any]:
    """Power-cycle with the console held at the Flash level, wait for the app
    banner (returns as soon as it appears), then probe AT.

    ok requires AT -> OK when probe_at, else just the app banner.
    """
    t_on = ppk.cycle(off_seconds, phase="boot")
    console.mark_power_on(t_on)
    bl = console.wait_for((BOOTLOADER_MARKER,), timeout=min(5.0, boot_timeout))
    app = console.wait_for(APP_MARKERS, timeout=max(0.0, boot_timeout - (time.time() - t_on)))
    detail: dict[str, Any] = {
        "bootloader_seen_s": round(bl[0], 2) if bl else None,
        "app_banner_s": round(app[0], 2) if app else None,
    }
    if app:
        ppk.set_phase("app")
        # let the banner finish before talking to the console
        time.sleep(1.5)

    at_ok = False
    at_lines: list[str] = []
    if probe_at:
        for _ in range(3):
            at_ok, at_lines = console.command("AT", timeout=2.0)
            if at_ok:
                break
        detail["at_ok"] = at_ok
        detail["at_response"] = "\n".join(at_lines)[:200]

    text = console.transcript()
    detail["openfw_seen"] = "openfw" in text
    for line in text.splitlines():
        if "Image Version:" in line:
            detail["image_version"] = line.split("Image Version:", 1)[1].strip()
            break
    detail["console_tail"] = text[-1500:]
    detail["ok"] = at_ok if probe_at else bool(app)
    print(
        f"boot: bootloader@{detail['bootloader_seen_s']}s app@{detail['app_banner_s']}s"
        + (f" AT ok={at_ok} {at_lines!r}" if probe_at else ""),
        flush=True,
    )
    return detail


def _open_console(args: argparse.Namespace, result: RunResult, rts: bool) -> ConsoleHold:
    log = Path(args.console_log) if args.console_log else None
    result.console_log = str(log) if log else None
    return ConsoleHold(args.uart, rts, log)


def _boot_rts(args: argparse.Namespace) -> bool:
    """RTS level for the requested boot mode (Flash = inverse of ISP)."""
    return args.isp_rts if args.boot_mode == "isp" else not args.isp_rts


def _finish(
    args: argparse.Namespace,
    result: RunResult,
    session: PpkSession,
    console: Optional[ConsoleHold],
) -> None:
    keep = bool(args.keep_power) and result.ok and result.command != "off"
    if console:
        console.close(keep_rts=keep)
    result.current_summary = session.summary()
    session.close(keep_power=keep)
    if keep:
        print(
            f"DUT left powered at {args.voltage_mv} mV (PPK2 not reset); "
            "run the 'off' command to cut power",
            flush=True,
        )


def _hold(args: argparse.Namespace, session: PpkSession) -> None:
    if args.hold_seconds > 0:
        print(f"Holding DUT ON for {args.hold_seconds}s (PPK2 + console held open)...", flush=True)
        session.set_phase("hold")
        time.sleep(args.hold_seconds)


def cmd_flash_run(args: argparse.Namespace, result: RunResult) -> int:
    hex_path = Path(args.hex).resolve()
    if not hex_path.is_file():
        result.error = f"hex not found: {hex_path}"
        result.ok = False
        return 2

    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    console: Optional[ConsoleHold] = None

    try:
        # --- arm (DUT stays off; the ISP cycle powers it) ---
        st = StepResult("arm", False, started_at=_utc_now())
        try:
            session.arm(current_log, dut_on=False)
            st.ok = True
        except Exception as e:
            st.error = str(e)
        st.finished_at = _utc_now()
        result.add(st)
        if not st.ok:
            return 1

        isp_rts = args.isp_rts
        # --- ISP sync (probe polarity if needed) ---
        st = StepResult("isp_sync", False, started_at=_utc_now())
        synced = isp_sync(args.uart, isp_rts, session, args.off_seconds)
        if not synced and args.auto_rts:
            alt = not isp_rts
            print(f"ISP sync failed; trying inverted RTS={alt}", flush=True)
            synced = isp_sync(args.uart, alt, session, args.off_seconds)
            if synced:
                isp_rts = alt
        st.ok = synced
        st.detail = {"isp_rts": isp_rts}
        if not synced:
            st.error = "ISP sync failed"
        st.finished_at = _utc_now()
        result.add(st)
        if not st.ok:
            return 1

        # --- flash ---
        st = StepResult("flash", False, started_at=_utc_now())
        try:
            st.detail = do_flash(args.uart, hex_path, isp_rts, session, args.off_seconds)
            st.ok = True
        except Exception as e:
            st.error = str(e)
        st.finished_at = _utc_now()
        result.add(st)
        if not st.ok:
            return 1

        # --- boot + AT (console held at the Flash level from here on) ---
        st = StepResult("boot", False, started_at=_utc_now())
        try:
            console = _open_console(args, result, rts=not isp_rts)
            boot = boot_and_probe(console, session, args.off_seconds, args.settle_seconds)
            st.ok = bool(boot.get("ok"))
            st.detail = boot
            if not st.ok:
                st.error = "no AT -> OK after boot" if boot.get("app_banner_s") else "app banner not seen"
        except Exception as e:
            st.error = str(e)
        st.finished_at = _utc_now()
        result.add(st)
        if not st.ok:
            return 1

        _hold(args, session)
        if args.power_off:
            session.dut_off()

        result.ok = True
        return 0
    finally:
        _finish(args, result, session, console)


def cmd_boot(args: argparse.Namespace, result: RunResult) -> int:
    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    console: Optional[ConsoleHold] = None
    try:
        console = _open_console(args, result, rts=not args.isp_rts)
        session.arm(current_log, dut_on=False)
        st = StepResult("boot", False, started_at=_utc_now())
        boot = boot_and_probe(console, session, args.off_seconds, args.settle_seconds)
        st.ok = bool(boot.get("ok"))
        st.detail = boot
        if not st.ok:
            st.error = "no AT -> OK after boot" if boot.get("app_banner_s") else "app banner not seen"
        st.finished_at = _utc_now()
        result.add(st)
        if st.ok:
            _hold(args, session)
        if args.power_off:
            session.dut_off()
        result.ok = st.ok
        return 0 if st.ok else 1
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        _finish(args, result, session, console)


def cmd_cycle(args: argparse.Namespace, result: RunResult) -> int:
    """Power-cycle. With the console held (default) waits for the app banner."""
    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    console: Optional[ConsoleHold] = None
    try:
        if not args.no_uart:
            console = _open_console(args, result, rts=_boot_rts(args))
        session.arm(current_log, dut_on=False)

        st = StepResult("cycle", False, started_at=_utc_now())
        if console and args.boot_mode == "flash":
            st.detail = boot_and_probe(console, session, args.off_seconds, args.settle_seconds, probe_at=False)
            st.ok = bool(st.detail.get("ok"))
            if not st.ok:
                st.error = "app banner not seen"
        else:
            session.cycle(args.off_seconds, phase="cycle")
            time.sleep(1.0)
            st.ok = True
            st.detail = {"boot_mode": "isp" if console else "unheld (RTS released = ISP level)"}
        avg = session.sampler.recent_avg(0.5) if session.sampler else None
        st.detail["avg_uA_after"] = None if avg is None else round(avg, 2)
        st.finished_at = _utc_now()
        result.add(st)

        if st.ok:
            _hold(args, session)
        if args.power_off:
            session.dut_off()
        result.ok = st.ok
        return 0 if st.ok else 1
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        _finish(args, result, session, console)


def cmd_monitor(args: argparse.Namespace, result: RunResult) -> int:
    """Power on (cold boot) and record current + console for --seconds."""
    current_log = Path(args.current_log)
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log)
    console: Optional[ConsoleHold] = None
    try:
        if not args.no_uart:
            console = _open_console(args, result, rts=_boot_rts(args))
        session.arm(current_log, dut_on=False)
        t_on = session.cycle(args.off_seconds, phase="boot")
        if console:
            console.mark_power_on(t_on)

        seconds = args.seconds
        print(f"Monitoring current for {seconds}s -> {current_log}", flush=True)
        app_s = None
        if console and args.boot_mode == "flash":
            app = console.wait_for(APP_MARKERS, timeout=min(seconds, args.settle_seconds))
            if app:
                app_s = round(app[0], 2)
                session.set_phase("app")
        remaining = seconds - (time.time() - t_on)
        if remaining > 0:
            time.sleep(remaining)
        st = StepResult("monitor", True, started_at=_utc_now(), finished_at=_utc_now())
        st.detail = {"seconds": seconds, "app_banner_s": app_s}
        result.add(st)
        if args.power_off:
            session.dut_off()
        result.ok = True
        return 0
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        _finish(args, result, session, console)


def cmd_on_off(args: argparse.Namespace, result: RunResult, want_on: bool) -> int:
    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    console: Optional[ConsoleHold] = None
    try:
        if want_on and not args.no_uart:
            console = _open_console(args, result, rts=_boot_rts(args))
        session.arm(current_log, dut_on=False)
        st = StepResult("on" if want_on else "off", True, started_at=_utc_now())
        if not want_on:
            session.dut_off()
        else:
            t_on = time.time()
            session.dut_on()
            if console:
                console.mark_power_on(t_on)
            _hold(args, session)
        st.finished_at = _utc_now()
        result.add(st)
        result.ok = True
        return 0
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        _finish(args, result, session, console)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pscb_ppk2_cli",
        description="PPK2 source-meter + RTS/ISP flash CLI for PS-CB-NA (agent-friendly JSON results).",
    )
    p.add_argument(
        "command",
        choices=["flash-run", "flash", "boot", "cycle", "on", "off", "monitor"],
        help="flash-run = ISP flash + boot + AT probe (main agent path)",
    )
    p.add_argument("--ppk", default="auto", help="PPK2 port or 'auto' (default)")
    p.add_argument("--uart", default=DEFAULT_UART, help=f"FTDI UART (default {DEFAULT_UART})")
    p.add_argument("--voltage-mv", type=int, default=DEFAULT_MV, help=f"Source voltage mV (default {DEFAULT_MV})")
    p.add_argument("--off-seconds", type=float, default=DEFAULT_OFF_S, help="DUT off time during cycle")
    p.add_argument(
        "--settle-seconds",
        type=float,
        default=DEFAULT_BOOT_TIMEOUT_S,
        help=f"Max wait for the app banner after power-on; returns as soon as it appears "
        f"(default {DEFAULT_BOOT_TIMEOUT_S:g}; the bootloader alone takes ~27 s)",
    )
    p.add_argument("--hold-seconds", type=float, default=0.0, help="Keep DUT ON (PPK2 + console held) after success")
    p.add_argument("--power-off", action="store_true", help="DUT OFF before exit (power drops at exit anyway)")
    p.add_argument(
        "--keep-power",
        action="store_true",
        help="On success leave the DUT powered after exit (PPK2 not reset) with RTS kept "
        "at the Flash level; cut it later with the 'off' command",
    )
    p.add_argument("--hex", type=str, default="", help="App Intel HEX for flash / flash-run")
    p.add_argument("--current-log", type=str, default="", help="JSONL path for current samples")
    p.add_argument("--console-log", type=str, default="", help="Timestamped console transcript path")
    p.add_argument("--result", type=str, default="", help="Write full result JSON here")
    p.add_argument(
        "--isp-rts",
        type=lambda s: s.lower() in ("1", "true", "yes"),
        default=DEFAULT_ISP_RTS,
        help="RTS level for ISP (default false). Flash mode uses the inverse.",
    )
    p.add_argument(
        "--auto-rts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="On ISP sync failure, try inverted RTS (default: on)",
    )
    p.add_argument(
        "--boot-mode",
        choices=["flash", "isp"],
        default="flash",
        help="cycle/on/monitor: BOOT0 level held on the console RTS while powered (default flash = run the app)",
    )
    p.add_argument(
        "--no-uart",
        action="store_true",
        help="cycle/on/monitor: do not open the console. RTS is then released = ISP level, "
        "so the board boots the ROM bootloader, not the app",
    )
    p.add_argument("--seconds", type=float, default=30.0, help="monitor duration (from power-on)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not (800 <= args.voltage_mv <= 5000):
        print("voltage-mv must be 800..5000", file=sys.stderr)
        return 2

    if args.command in ("flash", "flash-run") and not args.hex:
        print("--hex is required for flash / flash-run", file=sys.stderr)
        return 2

    if args.command == "on" and args.hold_seconds <= 0 and not args.keep_power:
        print(
            "'on' needs --hold-seconds N or --keep-power: otherwise DUT power drops "
            "as soon as this process closes the PPK2 port",
            file=sys.stderr,
        )
        return 2

    if not Path(args.uart).exists() and not str(args.uart).upper().startswith("COM"):
        print(f"UART not found: {args.uart}", file=sys.stderr)
        return 3

    try:
        ppk_port = _discover_ppk(args.ppk)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 3

    # Default logs under dragino/logs/
    logs_dir = _DRAGINO_ROOT / "logs"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if not args.current_log and args.command in ("flash-run", "flash", "monitor", "cycle", "boot", "on"):
        args.current_log = str(logs_dir / f"ppk2-current-{stamp}.jsonl")
    if not args.result and args.command in ("flash-run", "flash", "boot", "monitor"):
        args.result = str(logs_dir / f"ppk2-result-{stamp}.json")
    if not args.console_log and args.command in ("flash-run", "flash", "monitor", "cycle", "boot", "on"):
        args.console_log = str(logs_dir / f"console-{stamp}.log")

    result = RunResult(
        ok=False,
        command=args.command,
        started_at=_utc_now(),
        voltage_mv=args.voltage_mv,
        ppk_port=ppk_port,
        uart=args.uart,
    )

    # 'flash' is an alias of flash-run (agents always want the boot verify)
    cmd = args.command
    if cmd == "flash":
        cmd = "flash-run"

    try:
        if cmd == "flash-run":
            rc = cmd_flash_run(args, result)
        elif cmd == "cycle":
            rc = cmd_cycle(args, result)
        elif cmd == "monitor":
            rc = cmd_monitor(args, result)
        elif cmd == "boot":
            rc = cmd_boot(args, result)
        elif cmd == "on":
            rc = cmd_on_off(args, result, want_on=True)
        elif cmd == "off":
            rc = cmd_on_off(args, result, want_on=False)
        else:
            print(f"unknown command {cmd}", file=sys.stderr)
            return 2
    except KeyboardInterrupt:
        result.ok = False
        result.error = "interrupted"
        rc = 1
    except Exception as e:
        result.ok = False
        result.error = str(e)
        rc = 1
        print(f"FATAL: {e}", file=sys.stderr)

    result_path = Path(args.result) if args.result else None
    result.write(result_path)
    if result_path:
        print(f"Wrote result -> {result_path}", flush=True)
    if result.current_log:
        print(f"Current log -> {result.current_log}", flush=True)
    if result.console_log:
        print(f"Console log -> {result.console_log}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
