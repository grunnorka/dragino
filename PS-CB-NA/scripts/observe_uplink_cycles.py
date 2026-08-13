#!/usr/bin/env python3
"""Unlock COM, verify PRO/SERVADDR/TDC, observe N uplink cycles (no config writes).

Uses shared dragino_uart (stable) + uplink_classify.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from dragino_uart import (  # noqa: E402
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    send_line,
    unlock,
    wait_idle,
)
from uplink_classify import MARKERS, classify, note_marker  # noqa: E402

EXPECTED = {
    "pro": "3,5",
    "servaddr": "66.33.22.220,33239",
    "tdc": "120",
    "fw": "v1.2.0",
}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main() -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "COM8"))
    ap.add_argument("--baud", type=int, default=int(os.environ.get("DRAGINO_BAUD", "9600")))
    ap.add_argument("--pin", default="")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--max-seconds", type=float, default=12 * 60)
    args = ap.parse_args()

    pin = resolve_pin(args.pin, device="ps-cb")
    if not pin:
        print("ERROR: missing DRAGINO_PIN", file=sys.stderr)
        return 2

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpath = logs / f"{stamp}_pscb_observe.raw.log"
    summary_path = logs / f"{stamp}_pscb_observe.summary.json"
    print(f"Log={logpath}", flush=True)

    ser = open_serial(args.port, args.baud, timeout=0.25)
    buf = LineBuffer()
    summary: dict = {"expected": EXPECTED, "verify": {}, "cycles": [], "started": utc()}

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***")
        row = f"{utc()} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as fh:
            fh.write(row + "\n")

    def drain(s: float) -> list[str]:
        return read_for(ser, s, buf, lambda L: log("RX", L))

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        shown = "***PIN***" if cmd.strip() in (pin, f"AT+PIN={pin}") else cmd
        log("TX", shown)
        send_line(ser, cmd)
        return drain(wait)

    log("SYS", "WAIT_IDLE")
    wait_idle(ser, 90.0, on_line=lambda L: log("RX", L))

    result = unlock(
        ser,
        pin,
        policy="stable",
        timeout=120.0,
        on_line=lambda L: log("RX", L),
        on_tx=lambda L: log("TX", L),
    )
    if not result.ok:
        log("SYS", f"UNLOCK_FAILED {result.hint}")
        ser.close()
        return 4

    def q(cmd: str) -> str:
        vals = []
        for L in send(cmd, 2.0):
            t = L.strip()
            if not t or t == "OK" or t.startswith(("AT+", "[", "Attention")):
                continue
            if "assword" in t or "Searching" in t or "NB " in t:
                continue
            vals.append(t)
        return vals[0] if vals else ""

    verify = {
        "pro": q("AT+PRO=?"),
        "servaddr": q("AT+SERVADDR=?"),
        "tdc": q("AT+TDC=?"),
        "ver": q("AT+VER=?") or q("AT+FW=?"),
        "csq": q("AT+CSQ"),
        "client": q("AT+CLIENT=?"),
        "pub": q("AT+PUBTOPIC=?"),
    }
    if not verify["csq"] or "CSQ" not in verify["csq"].upper():
        lines = send("AT+CSQ", 2.0)
        for L in lines:
            if "CSQ" in L.upper():
                verify["csq"] = L.strip()
    summary["verify"] = verify
    match = {
        "pro_ok": EXPECTED["pro"] in (verify["pro"] or ""),
        "servaddr_ok": EXPECTED["servaddr"] in (verify["servaddr"] or ""),
        "tdc_ok": (verify["tdc"] or "") == EXPECTED["tdc"],
        "fw_ok": EXPECTED["fw"].lstrip("v") in (verify["ver"] or "").lower()
        or EXPECTED["fw"] in (verify["ver"] or ""),
    }
    summary["match"] = match
    log("SYS", f"VERIFY {verify}")
    log("SYS", f"MATCH {match}")

    send("AT+DEBUG=1", 1.2)

    deadline = time.monotonic() + args.max_seconds
    cycle: dict | None = None
    log("SYS", f"MONITOR {args.cycles} cycles max={args.max_seconds}s")

    while time.monotonic() < deadline and len(summary["cycles"]) < args.cycles:
        for L in drain(1.0):
            for key, rx in MARKERS:
                if not rx.search(L):
                    continue
                if key == "upload_start":
                    if cycle and "end" not in cycle:
                        cycle["end"] = {"ts": utc(), "line": "interrupted"}
                        cycle["class"] = classify(cycle)
                        summary["cycles"].append(cycle)
                        log("MARK", f"CYCLE_END interrupted {cycle.get('n')}")
                    cycle = {
                        "n": len(summary["cycles"]) + 1,
                        "start": utc(),
                    }
                    log("MARK", f"CYCLE_START {cycle['n']}")
                    continue
                if key == "upload_end":
                    note_marker(cycle, key, L, utc())
                    if cycle:
                        cycle["end"] = {"ts": utc(), "line": L[:120]}
                        cycle["class"] = classify(cycle)
                        summary["cycles"].append(cycle)
                        log("MARK", f"CYCLE_END {cycle['n']} class={cycle['class']}")
                        cycle = None
                    continue
                note_marker(cycle, key, L, utc())
                if cycle:
                    log("MARK", f"CYCLE{cycle['n']} {key}")

    if cycle:
        cycle["end"] = {"ts": utc(), "line": "timeout"}
        cycle["class"] = classify(cycle)
        summary["cycles"].append(cycle)
        log("MARK", f"CYCLE_END timeout {cycle['n']} class={cycle['class']}")

    result2 = unlock(
        ser,
        pin,
        policy="stable",
        timeout=60.0,
        on_line=lambda L: log("RX", L),
        on_tx=lambda L: log("TX", L),
    )
    if result2.ok:
        summary["final"] = {
            "pro": q("AT+PRO=?"),
            "servaddr": q("AT+SERVADDR=?"),
            "tdc": q("AT+TDC=?"),
        }
        log("SYS", f"FINAL {summary['final']}")

    summary["finished"] = utc()
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ser.close()
    log("SYS", "PORT_CLOSED")

    print("=== SUMMARY ===", flush=True)
    print(f"VERIFY={verify}", flush=True)
    print(f"MATCH={match}", flush=True)
    for c in summary["cycles"]:
        keys = [
            k
            for k in (
                "signal_strength",
                "network_attach",
                "mqtt_connect",
                "upload_ok",
                "subscribe_ok",
                "failed_send",
                "failed_tcp_close",
                "csq_99",
            )
            if c.get(k)
        ]
        print(
            f"CYCLE {c['n']}: class={c.get('class')} start={c.get('start')} "
            f"markers={keys}",
            flush=True,
        )
        for k in keys:
            ev = c[k]
            print(f"  {k}: {ev['ts']} | {ev['line']}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"SUMMARY_JSON={summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
