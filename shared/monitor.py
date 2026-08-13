#!/usr/bin/env python3
"""
Dragino PS-CB-NA UART logger (Windows USB-TTL).

Priorities:
  1) Capture ALL serial data (nothing dropped) to timestamped raw .log + JSONL
  2) Optional modem AT polling (CSQ / radio / registration / identity)
  3) Optional cycle (TDC) read / set via --set-cycle
  4) Optional GPS enable via --set-gps / --set-gnsst / --set-gtdc
  5) Minimal live status (logging first)

Default UART: COM8 @ 9600 8N1 (Dragino NB-IoT console).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a local .env into os.environ (no override)."""
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


def load_local_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_pin(cli_pin: str) -> str:
    """CLI --pin wins; else DRAGINO_PIN / PIN from env, .env, or config.local.json."""
    if cli_pin:
        return cli_pin
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

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial required: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


# --- Cycle / uplink interval (from Dragino PS-CB-NA / -CB wiki) ---
# AT+TDC  = application data transmission interval in SECONDS
# Default = 7200 (2 hours). Example: AT+TDC=120  -> uplink every 120s
# Query:  AT+TDC=?
# Also appears in AT+CFG dump. Downlink 0x01 + 3-byte seconds also sets TDC.
#
# --- GPS (default AT+GPS=0; indoor fix often fails → lat/lon 0) ---
# AT+GPS=1|0   enable/disable GPS (fix on activate + every AT+GTDC hours)
# AT+GNSST=N   GNSS search window in seconds (default 30; extend if no fix)
# AT+GTDC=H    GPS reposition interval in HOURS (not TDC seconds)
# Caveats: needs outdoor sky view + GPS antenna seated; cold start can use full GNSST;
# GNSS burns battery — leave AT+GPS=0 when position not needed.

DEFAULT_BAUD = 9600
DEFAULT_PORT = "COM8"

# Quectel BG95 / Dragino console queries used for modem status
MODEM_POLL_CMDS = [
    "AT+CSQ",
    "AT+CEREG?",
    "AT+CGREG?",
    "AT+QCSQ",
    "AT+QENG=\"servingcell\"",
    "AT+CGSN",   # IMEI (module)
    "AT+CIMI",   # IMSI
    "AT+LDATA",  # last upload (Dragino)
]

RE_CSQ = re.compile(r"\+CSQ:\s*(\d{1,2})\s*,\s*(\d+)|CSQ[=:\s]+(\d{1,2})", re.I)
RE_CEREG = re.compile(r"\+CEREG:\s*(\d+)\s*,\s*(\d+)", re.I)
RE_RSRP = re.compile(r"RSRP[=:\s,\"']+(-?\d+(?:\.\d+)?)", re.I)
RE_RSRQ = re.compile(r"RSRQ[=:\s,\"']+(-?\d+(?:\.\d+)?)", re.I)
RE_SINR = re.compile(r"SINR[=:\s,\"']+(-?\d+(?:\.\d+)?)", re.I)
RE_TDC = re.compile(r"(?:AT\+TDC=|\+TDC:|TDC[=:\s]+)(\d+)", re.I)
RE_GPS = re.compile(r"(?:AT\+GPS=|\+GPS:|GPS[=:\s]+)([01])\b", re.I)
RE_GNSST = re.compile(r"(?:AT\+GNSST=|\+GNSST:|GNSST[=:\s]+)(\d+)", re.I)
RE_GTDC = re.compile(r"(?:AT\+GTDC=|\+GTDC:|GTDC[=:\s]+)(\d+)", re.I)
RE_LAT = re.compile(r'"latitude"\s*:\s*(-?\d+(?:\.\d+)?)', re.I)
RE_LON = re.compile(r'"longitude"\s*:\s*(-?\d+(?:\.\d+)?)', re.I)
RE_GPS_TIME = re.compile(r'"gps_time"\s*:\s*"([^"]+)"', re.I)
RE_SIGNAL_JSON = re.compile(r'"signal"\s*:\s*(\d{1,2})', re.I)
RE_BAT_JSON = re.compile(r'"battery"\s*:\s*(\d+(?:\.\d+)?)', re.I)
RE_IMEI = re.compile(r"(?:IMEI[=:\s\"']+|^\s*)(\d{15})\s*$", re.I)
RE_IMSI = re.compile(r"(?:IMSI[=:\s\"']+|^\s*)(\d{14,15})\s*$", re.I)
RE_PASSWORD_PROMPT = re.compile(r"password|AT\+PIN|please\s+input|enter\s+pass", re.I)
RE_PASSWORD_OK = re.compile(r"Password\s+Correct", re.I)
RE_BOOTLOADER = re.compile(r"bootloader", re.I)
RE_MODEM_FAIL = re.compile(r"NBIOT\s+did\s+not\s+respond", re.I)

try:
    from uplink_classify import (  # type: ignore
        CLASS_FALSE_POSITIVE,
        is_failed_send_line,
        is_upload_success_line,
    )
except ImportError:  # pragma: no cover
    CLASS_FALSE_POSITIVE = "false-positive teardown after success"

    def is_failed_send_line(line: str) -> bool:
        return "failed to send" in line.lower()

    def is_upload_success_line(line: str) -> bool:
        return "upload data successfully" in line.lower()


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def csq_to_dbm(csq: Optional[int]) -> Optional[float]:
    if csq is None or csq == 99:
        return None
    if 0 <= csq <= 31:
        return -113.0 + 2.0 * csq
    return None


@dataclass
class State:
    port_open: bool = False
    reconnects: int = 0
    bytes_rx: int = 0
    lines_rx: int = 0
    last_rx_ts: Optional[str] = None
    connection_state: str = "unknown"
    csq: Optional[int] = None
    csq_dbm: Optional[float] = None
    rsrp: Optional[float] = None
    rsrq: Optional[float] = None
    sinr: Optional[float] = None
    cereg_stat: Optional[int] = None
    tdc_s: Optional[int] = None
    gps_on: Optional[int] = None
    gnsst_s: Optional[int] = None
    gtdc_h: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    gps_time: Optional[str] = None
    battery_v: Optional[float] = None
    imei: Optional[str] = None
    imsi: Optional[str] = None
    uplink_ok: int = 0
    uplink_fail: int = 0
    uplink_teardown_fp: int = 0  # Failed to send after upload_ok
    errors: int = 0
    unlocked: bool = False
    uart_phase: str = "idle"
    unlock_blocked: bool = False
    cycle_had_upload_ok: bool = False
    notes: Dict[str, int] = field(default_factory=dict)


class Logger:
    """Durable logs: raw .log (every byte stream as lines), JSONL (every record), optional metrics CSV."""

    def __init__(self, log_dir: Path, session: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = log_dir / f"{session}.raw.log"
        self.jsonl_path = log_dir / f"{session}.jsonl"
        self.csv_path = log_dir / f"{session}.metrics.csv"
        self._raw = self.raw_path.open("a", encoding="utf-8", newline="\n", buffering=1)
        self._jsonl = self.jsonl_path.open("a", encoding="utf-8", newline="\n", buffering=1)
        new_csv = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        self._csv_f = self.csv_path.open("a", encoding="utf-8", newline="")
        self._csv = csv.DictWriter(
            self._csv_f,
            fieldnames=[
                "ts",
                "event",
                "connection_state",
                "csq",
                "csq_dbm",
                "rsrp",
                "rsrq",
                "sinr",
                "cereg_stat",
                "tdc_s",
                "gps_on",
                "latitude",
                "longitude",
                "gps_time",
                "battery_v",
                "uplink_ok",
                "uplink_fail",
                "errors",
                "reconnects",
                "bytes_rx",
                "line",
            ],
        )
        if new_csv:
            self._csv.writeheader()
            self._csv_f.flush()

    def raw(self, direction: str, text: str, ts: Optional[str] = None) -> None:
        self._raw.write(f"{ts or utc_iso()} {direction} {text}\n")
        self._raw.flush()

    def event(self, record: Dict[str, Any], state: State, also_csv: bool = True) -> None:
        rec = dict(record)
        rec.setdefault("ts", utc_iso())
        self._jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._jsonl.flush()
        if also_csv:
            self._csv.writerow(
                {
                    "ts": rec.get("ts"),
                    "event": rec.get("event"),
                    "connection_state": state.connection_state,
                    "csq": state.csq,
                    "csq_dbm": state.csq_dbm,
                    "rsrp": state.rsrp,
                    "rsrq": state.rsrq,
                    "sinr": state.sinr,
                    "cereg_stat": state.cereg_stat,
                    "tdc_s": state.tdc_s,
                    "gps_on": state.gps_on,
                    "latitude": state.latitude,
                    "longitude": state.longitude,
                    "gps_time": state.gps_time,
                    "battery_v": state.battery_v,
                    "uplink_ok": state.uplink_ok,
                    "uplink_fail": state.uplink_fail,
                    "errors": state.errors,
                    "reconnects": state.reconnects,
                    "bytes_rx": state.bytes_rx,
                    "line": (rec.get("line") or "")[:500],
                }
            )
            self._csv_f.flush()

    def close(self) -> None:
        for fh in (self._raw, self._jsonl, self._csv_f):
            try:
                fh.close()
            except OSError:
                pass


class Parser:
    def __init__(self, state: State) -> None:
        self.s = state

    def parse_line(self, line: str) -> List[Dict[str, Any]]:
        """Extract modem/status metrics; always returns at least a rx_line event for JSONL completeness."""
        s = self.s
        events: List[Dict[str, Any]] = []
        text = line.rstrip("\r\n")
        base = {"event": "rx_line", "line": text}

        m = RE_CSQ.search(text)
        if m:
            csq = int(m.group(1) or m.group(3))
            s.csq = csq
            s.csq_dbm = csq_to_dbm(csq)
            s.connection_state = "disconnected" if csq == 99 else "connected"
            events.append({**base, "event": "csq", "csq": csq, "csq_dbm": s.csq_dbm})

        m = RE_CEREG.search(text)
        if m:
            s.cereg_stat = int(m.group(2))
            # 1=registered home, 5=registered roaming
            if s.cereg_stat in (1, 5):
                s.connection_state = "connected"
            elif s.cereg_stat in (0, 2, 3, 4):
                s.connection_state = "attaching" if s.cereg_stat == 2 else "disconnected"
            events.append({**base, "event": "cereg", "cereg_n": int(m.group(1)), "cereg_stat": s.cereg_stat})

        for rx, attr, ev in (
            (RE_RSRP, "rsrp", "rsrp"),
            (RE_RSRQ, "rsrq", "rsrq"),
            (RE_SINR, "sinr", "sinr"),
        ):
            m = rx.search(text)
            if m:
                val = float(m.group(1))
                setattr(s, attr, val)
                events.append({**base, "event": ev, attr: val})

        m = RE_TDC.search(text)
        if m:
            s.tdc_s = int(m.group(1))
            events.append({**base, "event": "tdc", "tdc_s": s.tdc_s})

        m = RE_GPS.search(text)
        if m:
            s.gps_on = int(m.group(1))
            events.append({**base, "event": "gps", "gps_on": s.gps_on})

        m = RE_GNSST.search(text)
        if m:
            s.gnsst_s = int(m.group(1))
            events.append({**base, "event": "gnsst", "gnsst_s": s.gnsst_s})

        m = RE_GTDC.search(text)
        if m:
            s.gtdc_h = int(m.group(1))
            events.append({**base, "event": "gtdc", "gtdc_h": s.gtdc_h})

        lat_m = RE_LAT.search(text)
        lon_m = RE_LON.search(text)
        gps_t_m = RE_GPS_TIME.search(text)
        if lat_m or lon_m or gps_t_m:
            if lat_m:
                s.latitude = float(lat_m.group(1))
            if lon_m:
                s.longitude = float(lon_m.group(1))
            if gps_t_m:
                s.gps_time = gps_t_m.group(1)
            events.append(
                {
                    **base,
                    "event": "gps_fix",
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "gps_time": s.gps_time,
                }
            )

        m = RE_SIGNAL_JSON.search(text)
        if m:
            s.csq = int(m.group(1))
            s.csq_dbm = csq_to_dbm(s.csq)
            events.append({**base, "event": "json_signal", "csq": s.csq})

        m = RE_BAT_JSON.search(text)
        if m:
            bat = float(m.group(1))
            s.battery_v = bat / 1000.0 if bat > 20 else bat
            events.append({**base, "event": "battery", "battery_v": s.battery_v})

        # Lone 15-digit lines after CGSN / CIMI
        stripped = text.strip()
        if re.fullmatch(r"\d{15}", stripped):
            if not s.imei:
                s.imei = stripped
                events.append({**base, "event": "imei", "imei": stripped})
            elif stripped != s.imei and not s.imsi:
                s.imsi = stripped
                events.append({**base, "event": "imsi", "imsi": stripped})

        m = RE_IMEI.search(text)
        if m and "IMEI" in text.upper():
            s.imei = m.group(1)
            events.append({**base, "event": "imei", "imei": s.imei})

        low = text.lower()
        if RE_BOOTLOADER.search(text):
            s.uart_phase = "bootloader"
            events.append({**base, "event": "bootloader"})
        if RE_MODEM_FAIL.search(text):
            s.uart_phase = "modem_fail"
            s.unlock_blocked = True
            events.append({**base, "event": "modem_fail"})
        if "start of upload" in low or "upload start" in low:
            s.cycle_had_upload_ok = False
            s.uart_phase = "uploading"
        if "end of upload" in low or "power-off successful" in low:
            if s.uart_phase == "uploading":
                s.uart_phase = "ready"
        if is_upload_success_line(text) or any(
            k in low for k in ("send data ok", "uplink ok", "publish ok", "mqtt publish ok", "packet sent")
        ):
            s.uplink_ok += 1
            s.cycle_had_upload_ok = True
            events.append({**base, "event": "uplink_ok"})
        if is_failed_send_line(text) or any(
            k in low for k in ("send data fail", "send fail", "uplink fail", "publish fail", "send error")
        ):
            if s.cycle_had_upload_ok:
                s.uplink_teardown_fp += 1
                events.append({**base, "event": "uplink_teardown_fp", "class": CLASS_FALSE_POSITIVE})
            else:
                s.uplink_fail += 1
                events.append({**base, "event": "uplink_fail"})
        if re.search(r"\b(ERROR|AT_ERROR|FAIL|TIMEOUT)\b", text) and not text.strip().upper().startswith("AT+"):
            if not is_failed_send_line(text):
                s.errors += 1
                events.append({**base, "event": "error"})
        if any(k in low for k in ("network connected", "mqtt connected", "attached", "register success")):
            s.connection_state = "connected"
            events.append({**base, "event": "attach_ok"})
        if any(k in low for k in ("attach fail", "register fail", "no network", "mqtt disconnect")):
            s.connection_state = "disconnected"
            events.append({**base, "event": "attach_fail"})

        if not events:
            events.append(base)
        return events


class Monitor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state = State()
        self.parser = Parser(self.state)
        session = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log = Logger(Path(args.log_dir), session)
        self._ser: Optional[serial.Serial] = None
        self._buf = bytearray()
        self._stop = threading.Event()
        self._last_poll = 0.0
        self._cycle_done = False
        self._gps_done = False
        self._pin_sent = False
        self._last_unlock_retry = 0.0
        self._unlock_retries = 0
        print(f"Logs:\n  {self.log.raw_path}\n  {self.log.jsonl_path}\n  {self.log.csv_path}", flush=True)

    def open_port(self) -> None:
        ser = serial.Serial(
            port=self.args.port,
            baudrate=self.args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=2,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        try:
            ser.dtr = False
            ser.rts = False
        except Exception:
            pass
        # Large read buffer — prioritize not dropping data
        try:
            ser.set_buffer_size(rx_size=256 * 1024, tx_size=16 * 1024)
        except Exception:
            pass
        self._ser = ser
        self.state.port_open = True
        self.log.event(
            {"event": "port_open", "port": self.args.port, "baud": self.args.baud},
            self.state,
        )
        self.log.raw("SYS", f"PORT OPEN {self.args.port} @ {self.args.baud}")

    def close_port(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self.state.port_open = False

    def send(self, cmd: str) -> None:
        if not self._ser or not self._ser.is_open:
            return
        payload = (cmd.rstrip("\r\n") + "\r\n").encode("utf-8", errors="replace")
        self._ser.write(payload)
        self._ser.flush()
        ts = utc_iso()
        self.log.raw("TX", cmd, ts)
        self.log.event({"event": "tx", "line": cmd, "ts": ts}, self.state)

    def unlock_if_needed(self, line: str) -> None:
        if self.state.unlock_blocked:
            return
        if RE_BOOTLOADER.search(line):
            self.state.uart_phase = "bootloader"
            # Do not PIN-spam into bootloader
            return
        if RE_MODEM_FAIL.search(line):
            self.state.uart_phase = "modem_fail"
            self.state.unlock_blocked = True
            print(
                "NBIOT did not respond — stopping unlock retries "
                "(SIM/antenna/APN; or SW1=Flash if bootloader loops).",
                flush=True,
            )
            return
        if RE_PASSWORD_OK.search(line):
            if not self.state.unlocked:
                self.state.unlocked = True
                self.state.uart_phase = "unlocked"
                self.log.raw("SYS", "UNLOCK_OK Password Correct")
                print("UNLOCK_OK Password Correct", flush=True)
                if self.args.debug:
                    time.sleep(0.3)
                    self.send("AT+DEBUG=1")
            return
        if self.state.unlocked or not self.args.pin:
            return
        if self.state.uart_phase == "bootloader":
            return
        # Send PIN on password prompt only here; --unlock-now retries live in run()
        if RE_PASSWORD_PROMPT.search(line):
            now = time.monotonic()
            if self._pin_sent and now - self._last_unlock_retry < 2.0:
                return
            self._last_unlock_retry = now
            self.send(self.args.pin)
            self._pin_sent = True
            # unlocked stays False until Password Correct                    time.sleep(0.2)

    def maybe_configure_cycle(self) -> None:
        """Read TDC; optionally set with --set-cycle (seconds). Requires AT unlock."""
        if self._cycle_done:
            return
        if self.args.pin and not self.state.unlocked:
            return
        # Wait for any console traffic if we are still trying to wake a sleeping node
        if self.args.unlock_now and self.state.lines_rx == 0 and self._unlock_retries < 12:
            return
        self._cycle_done = True  # claim so concurrent timers do not double-apply

        self.send("AT+TDC=?")
        time.sleep(0.5)
        if self.args.set_cycle is not None:
            sec = int(self.args.set_cycle)
            print(f"Setting uplink cycle AT+TDC={sec} (seconds)...", flush=True)
            self.send(f"AT+TDC={sec}")
            time.sleep(0.4)
            self.send("AT+TDC=?")
            time.sleep(0.3)
            if self.args.reset_after_set:
                print("Sending ATZ to apply settings...", flush=True)
                self.send("ATZ")
            self.log.event(
                {
                    "event": "set_cycle",
                    "tdc_s": sec,
                    "note": "AT+TDC sets uplink interval in seconds; stored in device flash; ATZ optional",
                },
                self.state,
            )

    def maybe_configure_gps(self) -> None:
        """Query/set GPS via --set-gps / --set-gnsst / --set-gtdc. Requires AT unlock."""
        if self._gps_done:
            return
        want = (
            self.args.set_gps is not None
            or self.args.set_gnsst is not None
            or self.args.set_gtdc is not None
        )
        if not want:
            self._gps_done = True
            return
        if self.args.pin and not self.state.unlocked:
            return
        if self.args.unlock_now and self.state.lines_rx == 0 and self._unlock_retries < 12:
            return
        self._gps_done = True

        print("Querying GPS settings...", flush=True)
        self.send("AT+GPS=?")
        time.sleep(1.5)
        self.send("AT+GNSST=?")
        time.sleep(1.2)
        self.send("AT+GTDC=?")
        time.sleep(1.2)

        if self.args.set_gps is not None:
            v = int(self.args.set_gps)
            print(f"Setting AT+GPS={v}...", flush=True)
            self.send(f"AT+GPS={v}")
            time.sleep(1.5)
            self.send("AT+GPS=?")
            time.sleep(1.5)

        if self.args.set_gnsst is not None:
            sec = int(self.args.set_gnsst)
            print(f"Setting AT+GNSST={sec} (GNSS search seconds)...", flush=True)
            self.send(f"AT+GNSST={sec}")
            time.sleep(1.5)
            self.send("AT+GNSST=?")
            time.sleep(1.2)

        if self.args.set_gtdc is not None:
            hours = int(self.args.set_gtdc)
            print(f"Setting AT+GTDC={hours} (GPS interval hours)...", flush=True)
            self.send(f"AT+GTDC={hours}")
            time.sleep(1.5)
            self.send("AT+GTDC=?")
            time.sleep(1.2)

        # CFG dump confirms GPS line among other settings
        self.send("AT+CFG")
        time.sleep(3.0)
        self.log.event(
            {
                "event": "set_gps",
                "gps": self.args.set_gps,
                "gnsst": self.args.set_gnsst,
                "gtdc": self.args.set_gtdc,
                "note": "GPS fix runs on activate + every GTDC hours; needs outdoor sky view",
            },
            self.state,
        )

    def poll_modem(self) -> None:
        if not self.args.poll:
            return
        if self.args.pin and not self.state.unlocked:
            return
        now = time.monotonic()
        if now - self._last_poll < self.args.poll:
            return
        self._last_poll = now
        self.log.event({"event": "poll_start"}, self.state)
        for cmd in MODEM_POLL_CMDS:
            self.send(cmd)
            time.sleep(0.12)
        # Dragino-specific helpful reads
        self.send("AT+TDC=?")
        time.sleep(0.12)

    def handle_line(self, line: str) -> None:
        ts = utc_iso()
        self.state.lines_rx += 1
        self.state.last_rx_ts = ts
        self.log.raw("RX", line, ts)
        for ev in self.parser.parse_line(line):
            ev["ts"] = ts
            # Always log every rx_line to JSONL (full capture of parsed stream)
            self.log.event(ev, self.state, also_csv=(ev.get("event") != "rx_line") or self.args.log_all_csv)
        was_locked = not self.state.unlocked
        self.unlock_if_needed(line)
        # After first successful unlock, or first RX after silent wake retries, configure once
        if was_locked and self.state.unlocked:
            if not self._cycle_done:
                threading.Timer(0.8, self.maybe_configure_cycle).start()
            if not self._gps_done:
                threading.Timer(1.6, self.maybe_configure_gps).start()
        elif self.state.lines_rx == 1:
            if not self._cycle_done:
                threading.Timer(0.5, self.maybe_configure_cycle).start()
            if not self._gps_done:
                threading.Timer(1.2, self.maybe_configure_gps).start()

    def consume(self, data: bytes) -> None:
        """Buffer and split on newlines; flush incomplete lines only on long idle via caller."""
        self.state.bytes_rx += len(data)
        self._buf.extend(data)
        while True:
            # Find earliest line ending
            i_crlf = self._buf.find(b"\r\n")
            i_lf = self._buf.find(b"\n")
            i_cr = self._buf.find(b"\r")
            best = None  # (index, seplen)
            for idx, seplen in ((i_crlf, 2), (i_lf, 1), (i_cr, 1)):
                if idx < 0:
                    continue
                if i_crlf >= 0 and idx == i_cr and idx == i_crlf:
                    continue  # CR of CRLF handled by CRLF
                if best is None or idx < best[0]:
                    best = (idx, seplen)
            if best is None:
                break
            idx, seplen = best
            if i_crlf >= 0 and idx == i_crlf:
                seplen = 2
            line = self._buf[:idx].decode("utf-8", errors="replace")
            del self._buf[: idx + seplen]
            self.handle_line(line)

    def flush_partial(self) -> None:
        if self._buf:
            line = self._buf.decode("utf-8", errors="replace")
            self._buf.clear()
            if line.strip():
                self.handle_line(line)

    def print_status(self) -> None:
        s = self.state
        age = "-"
        if s.last_rx_ts:
            try:
                last = datetime.fromisoformat(s.last_rx_ts.replace("Z", "+00:00"))
                age = f"{(datetime.now(timezone.utc) - last).total_seconds():.1f}s"
            except ValueError:
                pass
        print(
            f"[{utc_iso()}] port={'UP' if s.port_open else 'DOWN'} state={s.connection_state} "
            f"rx={s.bytes_rx}B/{s.lines_rx}ln age={age} CSQ={s.csq} "
            f"RSRP={s.rsrp} TDC={s.tdc_s}s GPS={s.gps_on} "
            f"lat={s.latitude} lon={s.longitude} reconn={s.reconnects} "
            f"up={s.uplink_ok}/{s.uplink_fail} fp={s.uplink_teardown_fp} "
            f"unlock={'Y' if s.unlocked else 'N'} phase={s.uart_phase}",
            flush=True,
        )

    def run(self) -> int:
        backoff = 1.0
        last_status = 0.0
        last_byte = time.monotonic()
        scheduled_cycle = False
        scheduled_gps = False

        # If unlock-now, send PIN soon after first open
        try:
            while not self._stop.is_set():
                try:
                    if self._ser is None or not self._ser.is_open:
                        self.open_port()
                        backoff = 1.0
                        scheduled_cycle = False
                        scheduled_gps = False
                        self._cycle_done = False
                        self._gps_done = False
                        self._pin_sent = False
                        self.state.unlocked = False
                        self.state.unlock_blocked = False
                        self.state.uart_phase = "idle"
                        if self.args.pin and self.args.unlock_now:
                            time.sleep(0.5)
                            self.send(self.args.pin)
                            self._pin_sent = True
                            # Do NOT set unlocked until Password Correct
                        if (self.state.unlocked or not self.args.pin) and not scheduled_cycle:
                            threading.Timer(1.0, self.maybe_configure_cycle).start()
                            scheduled_cycle = True
                        if (self.state.unlocked or not self.args.pin) and not scheduled_gps:
                            threading.Timer(2.0, self.maybe_configure_gps).start()
                            scheduled_gps = True

                    assert self._ser is not None
                    n = self._ser.in_waiting
                    if n:
                        chunk = self._ser.read(n)
                        self.consume(chunk)
                        last_byte = time.monotonic()
                    else:
                        # short blocking read to avoid busy-spin; still captures promptly
                        chunk = self._ser.read(4096)
                        if chunk:
                            self.consume(chunk)
                            last_byte = time.monotonic()
                        elif time.monotonic() - last_byte > 2.0 and self._buf:
                            # incomplete line sitting in buffer — keep it (don't drop);
                            # only flush if idle a long time so binary-ish bursts still coalesce
                            if time.monotonic() - last_byte > 5.0:
                                self.flush_partial()
                                last_byte = time.monotonic()

                    self.poll_modem()

                    # If unlock-now and device may be asleep: re-send PIN until Password Correct
                    if (
                        self.args.unlock_now
                        and self.args.pin
                        and not self.state.unlocked
                        and not self.state.unlock_blocked
                        and self.state.uart_phase != "bootloader"
                        and self._unlock_retries < 12
                    ):
                        now_m = time.monotonic()
                        if now_m - self._last_unlock_retry >= 3.0:
                            self._last_unlock_retry = now_m
                            self._unlock_retries += 1
                            print(
                                f"Waiting for Password Correct — unlock retry "
                                f"{self._unlock_retries}/12 (wake with ACT 1–3s)...",
                                flush=True,
                            )
                            self.send(self.args.pin)
                            self._pin_sent = True

                    # After unlock, arm one-shot TDC/GPS configure
                    if self.state.unlocked and not scheduled_cycle:
                        threading.Timer(0.5, self.maybe_configure_cycle).start()
                        scheduled_cycle = True
                    if self.state.unlocked and not scheduled_gps:
                        threading.Timer(1.0, self.maybe_configure_gps).start()
                        scheduled_gps = True

                    now = time.monotonic()
                    if now - last_status >= self.args.status_every:
                        self.print_status()
                        last_status = now

                except (serial.SerialException, OSError) as exc:
                    self.state.port_open = False
                    self.state.reconnects += 1
                    self.state.connection_state = "disconnected"
                    self.log.event({"event": "port_disconnect", "error": str(exc)}, self.state)
                    self.log.raw("SYS", f"PORT LOST: {exc}")
                    print(f"Port lost: {exc} — retry in {backoff:.1f}s", flush=True)
                    self.close_port()
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 15.0)
        except KeyboardInterrupt:
            print("\nStopping...", flush=True)
        finally:
            self._stop.set()
            self.flush_partial()
            self.close_port()
            self.log.close()
            self.print_status()
            print(f"Saved under {self.args.log_dir}", flush=True)
        return 0


def smoke(port: str, baud: int, seconds: float = 3.0) -> int:
    print(f"Smoke open {port} @ {baud} for {seconds}s...")
    try:
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.5)
        try:
            ser.dtr = False
            ser.rts = False
        except Exception:
            pass
        end = time.monotonic() + seconds
        n = 0
        while time.monotonic() < end:
            data = ser.read(512)
            if data:
                n += len(data)
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
        ser.close()
        print(f"\nOK — opened {port}, read {n} bytes")
        return 0
    except serial.SerialException as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PS-CB-NA full UART logger + optional modem poll / TDC cycle config"
    )
    p.add_argument("--port", default=DEFAULT_PORT)
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    p.add_argument("--log-dir", default="logs")
    p.add_argument(
        "--pin",
        default="",
        help="AT password (gift-box sticker). If omitted, uses DRAGINO_PIN from .env / env / config.local.json",
    )
    p.add_argument(
        "--unlock-now",
        action="store_true",
        help="Send PIN on connect; unlocked only after Password Correct",
    )
    p.add_argument("--debug", action="store_true", help="AT+DEBUG=1 after unlock")
    p.add_argument(
        "--poll",
        type=float,
        default=0,
        metavar="SEC",
        help="Poll modem AT cmds every SEC seconds (0=off). Needs --pin.",
    )
    p.add_argument(
        "--set-cycle",
        type=int,
        default=None,
        metavar="SEC",
        help="Set uplink interval via AT+TDC=SEC (e.g. 120). Not applied unless this flag is set.",
    )
    p.add_argument(
        "--set-gps",
        type=int,
        choices=[0, 1],
        default=None,
        metavar="0|1",
        help="Enable/disable GPS via AT+GPS=0|1. Not applied unless this flag is set.",
    )
    p.add_argument(
        "--set-gnsst",
        type=int,
        default=None,
        metavar="SEC",
        help="GNSS search window via AT+GNSST=SEC (default on device is often 30).",
    )
    p.add_argument(
        "--set-gtdc",
        type=int,
        default=None,
        metavar="HOURS",
        help="GPS reposition interval via AT+GTDC=HOURS (not TDC seconds).",
    )
    p.add_argument(
        "--reset-after-set",
        action="store_true",
        help="Send ATZ after --set-cycle (some settings need reset; TDC usually applies immediately)",
    )
    p.add_argument("--status-every", type=float, default=5.0, help="Seconds between one-line status")
    p.add_argument("--log-all-csv", action="store_true", help="Also put every rx_line into metrics CSV")
    p.add_argument("--list-ports", action="store_true")
    p.add_argument("--smoke", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(ROOT / ".env")
    args = build_parser().parse_args(argv)
    if not args.pin:
        args.pin = resolve_pin("")
    if args.list_ports:
        for p in list_ports.comports():
            print(f"{p.device:8}  {p.description}")
        return 0
    if args.smoke:
        return smoke(args.port, args.baud)
    if args.set_cycle is not None and args.set_cycle < 1:
        print("--set-cycle must be >= 1 second", file=sys.stderr)
        return 2
    if args.set_gnsst is not None and args.set_gnsst < 1:
        print("--set-gnsst must be >= 1 second", file=sys.stderr)
        return 2
    if args.set_gtdc is not None and args.set_gtdc < 1:
        print("--set-gtdc must be >= 1 hour", file=sys.stderr)
        return 2
    need_pin = args.poll or args.set_cycle is not None or args.set_gps is not None
    need_pin = need_pin or args.set_gnsst is not None or args.set_gtdc is not None
    if need_pin and not args.pin:
        print(
            "Warning: --poll / --set-cycle / --set-gps need a PIN (--pin or DRAGINO_PIN in .env).",
            file=sys.stderr,
        )
    return Monitor(args).run()


if __name__ == "__main__":
    sys.exit(main())
