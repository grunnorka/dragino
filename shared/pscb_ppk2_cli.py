#!/usr/bin/env python3
"""PS-CB-NA bench CLI: PPK2 source-meter power + RTS/ISP flash (agent-friendly).

Requires:
  - Nordic PPK2 on USB (source-meter / internal supply) powering the board
  - FTDI USB-TTL on the console/ISP UART, with RTS wired to the Flash/ISP
    (BOOT0) switch: RTS=False -> ISP, RTS=True -> Flash (empirically confirmed)

Important: the PPK2 serial port must stay open while DUT power is needed.
Closing the port (or exiting this process without --hold-seconds) typically
drops source output (LED back to green).

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

  .venv/bin/python shared/pscb_ppk2_cli.py cycle --voltage-mv 3700
  .venv/bin/python shared/pscb_ppk2_cli.py monitor --seconds 30 --current-log logs/i.jsonl
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
DEFAULT_MV = 3700
DEFAULT_OFF_S = 2.0
CONSOLE_BAUD = 9600
ISP_BAUD = 115200
# Empirically: RTS asserted (True) = Flash/normal; deasserted (False) = ISP
DEFAULT_ISP_RTS = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _discover_ppk(explicit: Optional[str]) -> str:
    if explicit and explicit != "auto":
        if not Path(explicit).exists() and not explicit.upper().startswith("COM"):
            raise FileNotFoundError(f"PPK2 port not found: {explicit}")
        return explicit
    devs = PPK2_API.list_devices()
    if not devs:
        raise FileNotFoundError("No PPK2 found (VID 1915:C00A)")
    return devs[0]


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


class CurrentLogger:
    """Background sampler: writes JSONL records while PPK2 is measuring."""

    def __init__(
        self,
        ppk: PPK2_API,
        path: Optional[Path],
        phase: str = "idle",
        io_lock: Optional[threading.Lock] = None,
    ):
        self.ppk = ppk
        self.path = path
        self.phase = phase
        self._io_lock = io_lock or threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._meta_lock = threading.Lock()
        self.last_avg_uA: Optional[float] = None
        self.sample_count = 0
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            # truncate / start fresh for this session
            path.write_text("", encoding="utf-8")

    def set_phase(self, phase: str) -> None:
        with self._meta_lock:
            self.phase = phase

    def start(self) -> None:
        if not self.path:
            return
        self._thread = threading.Thread(target=self._run, name="ppk2-current", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self) -> None:
        assert self.path is not None
        while not self._stop.is_set():
            try:
                with self._io_lock:
                    raw = self.ppk.get_data()
                    if not raw:
                        samples = []
                    else:
                        samples, _digital = self.ppk.get_samples(raw)
                if not samples:
                    time.sleep(0.05)
                    continue
                avg = sum(samples) / len(samples)
                with self._meta_lock:
                    phase = self.phase
                    self.last_avg_uA = avg
                    self.sample_count += len(samples)
                rec = {
                    "ts": time.time(),
                    "iso": _utc_now(),
                    "uA": round(avg, 3),
                    "n": len(samples),
                    "min_uA": round(min(samples), 3),
                    "max_uA": round(max(samples), 3),
                    "phase": phase,
                }
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, sort_keys=True) + "\n")
            except Exception as e:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "ts": time.time(),
                                "iso": _utc_now(),
                                "error": str(e),
                                "phase": self.phase,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                time.sleep(0.2)
            time.sleep(0.05)


class PpkSession:
    """Owns the PPK2 serial link for the whole CLI invocation."""

    def __init__(self, port: str, voltage_mv: int):
        self.port = port
        self.voltage_mv = voltage_mv
        self.ppk = PPK2_API(port, timeout=1, write_timeout=1)
        self._io = threading.Lock()
        self.logger: Optional[CurrentLogger] = None

    def close(self) -> None:
        if self.logger:
            self.logger.stop()
            self.logger = None
        with self._io:
            try:
                self.ppk._write_serial((PPK2_Command.AVERAGE_STOP,))
                time.sleep(0.05)
            except Exception:
                pass
            try:
                self.ppk.ser.close()
            except Exception:
                pass

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
        with self._io:
            try:
                self.ppk._write_serial((PPK2_Command.AVERAGE_STOP,))
                time.sleep(0.1)
                self.ppk._write_serial((PPK2_Command.DEVICE_RUNNING_SET, PPK2_Command.NO_OP))
                time.sleep(0.1)
                self._drain(0.6)
            except Exception:
                pass

    def _load_modifiers(self) -> bool:
        with self._io:
            for _ in range(5):
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
                        return True
                except Exception:
                    time.sleep(0.2)
        return False

    def arm(self, current_log: Optional[Path] = None) -> float:
        """Source-meter mode, set voltage, DUT ON, start measuring. Returns avg uA."""
        self._soft_stop()
        if not self._load_modifiers():
            print("WARNING: PPK2 modifiers not loaded; current values may be off", flush=True)
        if self.voltage_mv > 3600:
            print(
                f"WARNING: PS-CB-NA rated max ~3.6 V; using {self.voltage_mv} mV",
                flush=True,
            )
        with self._io:
            self.ppk.set_source_voltage(self.voltage_mv)
            time.sleep(0.2)
            self.ppk.use_source_meter()
            time.sleep(0.3)
            self.ppk.toggle_DUT_power("ON")
            time.sleep(0.2)
            self.ppk.start_measuring()
            time.sleep(0.4)

        self.logger = CurrentLogger(self.ppk, current_log, phase="armed", io_lock=self._io)
        self.logger.start()

        avg = self._spot_avg()
        print(
            f"PPK2 armed: source-meter @ {self.voltage_mv} mV on {self.port}"
            + (f" avg_uA={avg:.1f}" if avg is not None else ""),
            flush=True,
        )
        return avg if avg is not None else float("nan")

    def _spot_avg(self) -> Optional[float]:
        for _ in range(8):
            with self._io:
                raw = self.ppk.get_data()
                samples = []
                if raw:
                    samples, _ = self.ppk.get_samples(raw)
            if samples:
                return sum(samples) / len(samples)
            time.sleep(0.1)
        return None

    def dut_on(self) -> None:
        with self._io:
            self.ppk.toggle_DUT_power("ON")
        if self.logger:
            self.logger.set_phase("dut_on")
        print(f"DUT ON @ {self.voltage_mv} mV", flush=True)

    def dut_off(self) -> None:
        with self._io:
            self.ppk.toggle_DUT_power("OFF")
        if self.logger:
            self.logger.set_phase("dut_off")
        print("DUT OFF", flush=True)

    def cycle(self, off_seconds: float = DEFAULT_OFF_S, phase: str = "cycle") -> None:
        if self.logger:
            self.logger.set_phase(f"{phase}_off")
        with self._io:
            self.ppk.toggle_DUT_power("OFF")
        print(f"DUT OFF ({off_seconds}s)...", flush=True)
        time.sleep(off_seconds)
        with self._io:
            self.ppk.toggle_DUT_power("ON")
        if self.logger:
            self.logger.set_phase(f"{phase}_on")
        print(f"DUT ON @ {self.voltage_mv} mV", flush=True)


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

    if ppk.logger:
        ppk.logger.set_phase("isp_entry")
    print(f"ISP entry: RTS={isp_rts}", flush=True)
    conn = SerialConnection(uart, ISP_BAUD, "E")
    conn.connect()
    try:
        conn.serial_connection.dtr = False
        conn.serial_connection.rts = isp_rts
        time.sleep(0.05)
        conn.serial_connection.rts = isp_rts
        ppk.cycle(off_seconds, phase="isp")
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

    # Fresh ISP entry for the write
    hold = _hold_rts(uart, isp_rts, ISP_BAUD)
    try:
        if ppk.logger:
            ppk.logger.set_phase("flash_isp_cycle")
        ppk.cycle(off_seconds, phase="flash_isp")
        time.sleep(0.8)
        hold.rts = isp_rts
    finally:
        hold.close()

    data, addr = load_hex(hex_path)
    print(f"Flash {len(data)} bytes @ 0x{addr:08X}", flush=True)
    if ppk.logger:
        ppk.logger.set_phase("flash_write")
    # soft_isp=True: no zenity; board already in ROM ISP from power-cycle
    flash_image(uart, ISP_BAUD, data, addr, soft_isp=True)
    return {"bytes": len(data), "address": f"0x{addr:08X}"}


def do_boot(
    uart: str,
    flash_rts: bool,
    ppk: PpkSession,
    off_seconds: float,
    settle_s: float = 8.0,
) -> dict[str, Any]:
    """Power-cycle into Flash/normal mode and capture console banner + AT."""
    if ppk.logger:
        ppk.logger.set_phase("boot")
    hold = _hold_rts(uart, flash_rts, CONSOLE_BAUD)
    try:
        ppk.cycle(off_seconds, phase="boot")
        hold.rts = flash_rts
        buf = bytearray()
        deadline = time.time() + settle_s
        while time.time() < deadline:
            chunk = hold.read(256)
            if chunk:
                buf.extend(chunk)
        banner = bytes(buf)
        # Prefer probing after openfw heartbeat shows up; still send AT either way
        hold.reset_input_buffer()
        hold.write(b"AT\r\n")
        hold.flush()
        time.sleep(0.8)
        at_resp = hold.read(256)
        text = banner.decode("latin1", errors="replace")
        at_text = at_resp.decode("latin1", errors="replace")
        ok = ("OK" in at_text) or ("openfw" in text) or ("Password" in text)
        print(f"boot banner ({len(banner)}B): {banner[:200]!r}", flush=True)
        print(f"AT -> {at_resp!r}", flush=True)
        return {
            "ok": ok,
            "banner_preview": text[:400],
            "at_response": at_text.strip()[:200],
            "openfw_seen": "openfw" in text,
        }
    finally:
        hold.close()


def cmd_flash_run(args: argparse.Namespace, result: RunResult) -> int:
    hex_path = Path(args.hex).resolve()
    if not hex_path.is_file():
        result.error = f"hex not found: {hex_path}"
        result.ok = False
        return 2

    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None

    try:
        # --- arm ---
        st = StepResult("arm", False, started_at=_utc_now())
        try:
            avg = session.arm(current_log)
            st.ok = True
            st.detail = {"avg_uA": None if avg != avg else round(avg, 2)}
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
            detail = do_flash(args.uart, hex_path, isp_rts, session, args.off_seconds)
            st.ok = True
            st.detail = detail
        except Exception as e:
            st.error = str(e)
        st.finished_at = _utc_now()
        result.add(st)
        if not st.ok:
            return 1

        # --- boot + AT ---
        flash_rts = not isp_rts
        st = StepResult("boot", False, started_at=_utc_now())
        try:
            boot = do_boot(args.uart, flash_rts, session, args.off_seconds, args.settle_seconds)
            st.ok = bool(boot.get("ok"))
            st.detail = boot
            if not st.ok:
                st.error = "AT probe / openfw banner not seen"
        except Exception as e:
            st.error = str(e)
        st.finished_at = _utc_now()
        result.add(st)
        if not st.ok:
            return 1

        if args.hold_seconds > 0:
            print(f"Holding DUT ON for {args.hold_seconds}s (PPK2 port open)...", flush=True)
            if session.logger:
                session.logger.set_phase("hold")
            time.sleep(args.hold_seconds)

        if args.power_off:
            session.dut_off()

        result.ok = True
        return 0
    finally:
        session.close()


def cmd_cycle(args: argparse.Namespace, result: RunResult) -> int:
    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    try:
        st = StepResult("arm", False, started_at=_utc_now())
        avg = session.arm(current_log)
        st.ok = True
        st.detail = {"avg_uA": None if avg != avg else round(avg, 2)}
        st.finished_at = _utc_now()
        result.add(st)

        st = StepResult("cycle", False, started_at=_utc_now())
        session.cycle(args.off_seconds, phase="cycle")
        time.sleep(args.settle_seconds)
        avg2 = session._spot_avg()
        st.ok = True
        st.detail = {"avg_uA_after": None if avg2 is None else round(avg2, 2)}
        st.finished_at = _utc_now()
        result.add(st)

        if args.hold_seconds > 0:
            print(f"Holding {args.hold_seconds}s...", flush=True)
            time.sleep(args.hold_seconds)
        if args.power_off:
            session.dut_off()
        result.ok = True
        return 0
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        session.close()


def cmd_monitor(args: argparse.Namespace, result: RunResult) -> int:
    if not args.current_log:
        result.error = "--current-log is required for monitor"
        result.ok = False
        return 2
    current_log = Path(args.current_log)
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log)
    try:
        st = StepResult("arm", False, started_at=_utc_now())
        avg = session.arm(current_log)
        st.ok = True
        st.detail = {"avg_uA": None if avg != avg else round(avg, 2)}
        st.finished_at = _utc_now()
        result.add(st)

        seconds = args.seconds
        print(f"Monitoring current for {seconds}s -> {current_log}", flush=True)
        if session.logger:
            session.logger.set_phase("monitor")
        time.sleep(seconds)
        st = StepResult("monitor", True, started_at=_utc_now(), finished_at=_utc_now())
        st.detail = {
            "seconds": seconds,
            "samples": session.logger.sample_count if session.logger else 0,
            "last_avg_uA": session.logger.last_avg_uA if session.logger else None,
        }
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
        session.close()


def cmd_boot(args: argparse.Namespace, result: RunResult) -> int:
    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    try:
        session.arm(current_log)
        flash_rts = not args.isp_rts
        st = StepResult("boot", False, started_at=_utc_now())
        boot = do_boot(args.uart, flash_rts, session, args.off_seconds, args.settle_seconds)
        st.ok = bool(boot.get("ok"))
        st.detail = boot
        if not st.ok:
            st.error = "AT probe / openfw banner not seen"
        st.finished_at = _utc_now()
        result.add(st)
        if args.hold_seconds > 0:
            time.sleep(args.hold_seconds)
        if args.power_off:
            session.dut_off()
        result.ok = st.ok
        return 0 if st.ok else 1
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        session.close()


def cmd_on_off(args: argparse.Namespace, result: RunResult, want_on: bool) -> int:
    current_log = Path(args.current_log) if args.current_log else None
    session = PpkSession(result.ppk_port, args.voltage_mv)
    result.current_log = str(current_log) if current_log else None
    try:
        session.arm(current_log)
        st = StepResult("on" if want_on else "off", False, started_at=_utc_now())
        if want_on:
            session.dut_on()
        else:
            session.dut_off()
        st.ok = True
        st.finished_at = _utc_now()
        result.add(st)
        if want_on and args.hold_seconds > 0:
            print(f"Holding DUT ON for {args.hold_seconds}s...", flush=True)
            time.sleep(args.hold_seconds)
        result.ok = True
        return 0
    except Exception as e:
        result.ok = False
        result.error = str(e)
        return 1
    finally:
        session.close()


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
    p.add_argument("--voltage-mv", type=int, default=DEFAULT_MV, help="Source voltage mV (default 3700)")
    p.add_argument("--off-seconds", type=float, default=DEFAULT_OFF_S, help="DUT off time during cycle")
    p.add_argument("--settle-seconds", type=float, default=8.0, help="Post-boot listen window")
    p.add_argument("--hold-seconds", type=float, default=0.0, help="Keep DUT ON / port open after success")
    p.add_argument("--power-off", action="store_true", help="DUT OFF before exit (default: leave last state until port closes)")
    p.add_argument("--hex", type=str, default="", help="App Intel HEX for flash / flash-run")
    p.add_argument("--current-log", type=str, default="", help="JSONL path for current samples")
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
    p.add_argument("--seconds", type=float, default=30.0, help="monitor duration")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not (800 <= args.voltage_mv <= 5000):
        print("voltage-mv must be 800..5000", file=sys.stderr)
        return 2

    if args.command in ("flash", "flash-run") and not args.hex:
        print("--hex is required for flash / flash-run", file=sys.stderr)
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
    if not args.current_log and args.command in ("flash-run", "flash", "monitor", "cycle", "boot"):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.current_log = str(logs_dir / f"ppk2-current-{stamp}.jsonl")
    if not args.result and args.command in ("flash-run", "flash", "boot"):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.result = str(logs_dir / f"ppk2-result-{stamp}.json")

    result = RunResult(
        ok=False,
        command=args.command,
        started_at=_utc_now(),
        voltage_mv=args.voltage_mv,
        ppk_port=ppk_port,
        uart=args.uart,
    )

    # 'flash' is flash-run without requiring AT ok? Keep same pipeline but
    # still boot-verify — agents usually want flash-run. Alias flash -> flash-run.
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
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
