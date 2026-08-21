#!/usr/bin/env python3
"""AFK hypothesis loop for PS-CB-NA MQTT failure (debug session 2e7608).

Hypotheses:
  H1  MQOS=1 causes early fail → MQOS=0 should reach Successfully connected
  H2  MQTT TCP client broken → UDP PRO=2,5 datagram reaches a listener
  H3  MQTT framing/proxy issue → raw TCP PRO=4,5 opens/sends to a listener
  H4  Broker-specific → temporary HiveMQ :1883 can CONNECT (control)
  H5  Railway path only → baseline Railway cycle still no CONNACK

Always restores Railway MQTT (PRO=3,5, SERVADDR IP:33239, APN=NULL) at the end.
Writes NDJSON debug rows to .cursor/debug-2e7608.log (no secrets).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from at_session import at_cmd, read_cfg  # noqa: E402
from dragino_uart import (  # noqa: E402
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    unlock,
)
from railway_mqtt import load_config  # noqa: E402

DEBUG_LOG = ROOT / ".cursor" / "debug-2e7608.log"
SESSION = "2e7608"
DEVICE_ID = "ps-cb"
PUB = f"dragino/{DEVICE_ID}/up"
SUB = f"dragino/{DEVICE_ID}/down"

MARKERS = (
    "Successfully activated PDP context",
    "DNS configuration is successful",
    "Opened the MQTT client network successfully",
    "Successfully connected to the server",
    "Upload data successfully",
    "Failed to open the MQTT client network",
    "Failed to send",
    "MQTT configuration failed",
    "*****Upload start",
    "*****End of upload",
    "Protocol in Used:",
    "Signal Strength:",
)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def dlog(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "afk1") -> None:
    # #region agent log
    DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "sessionId": SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with DEBUG_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    # #endregion


class UdpSink:
    def __init__(self, port: int) -> None:
        self.port = port
        self.packets: list[dict] = []
        self._stop = threading.Event()
        self._thr: threading.Thread | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.port))
        sock.settimeout(0.5)
        self._sock = sock

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                rec = {
                    "ts": utc(),
                    "from": f"{addr[0]}:{addr[1]}",
                    "n": len(data),
                    "preview": data[:80].hex(),
                }
                self.packets.append(rec)
                print(f"  >>> UDP GOT {rec['from']} {rec['n']}B", flush=True)

        self._thr = threading.Thread(target=loop, daemon=True)
        self._thr.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass


class TcpSink:
    def __init__(self, port: int) -> None:
        self.port = port
        self.conns: list[dict] = []
        self._stop = threading.Event()
        self._thr: threading.Thread | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.port))
        sock.listen(5)
        sock.settimeout(0.5)
        self._sock = sock

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    conn, addr = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                conn.settimeout(8.0)
                try:
                    buf = b""
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        if len(buf) > 65536:
                            break
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                rec = {
                    "ts": utc(),
                    "from": f"{addr[0]}:{addr[1]}",
                    "n": len(buf),
                    "preview": buf[:80].hex(),
                }
                self.conns.append(rec)
                print(f"  >>> TCP GOT {rec['from']} {rec['n']}B", flush=True)

        self._thr = threading.Thread(target=loop, daemon=True)
        self._thr.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except Exception:
            pass


def classify(lines: list[str]) -> dict:
    blob = "\n".join(lines)
    return {
        "pdp_ok": "Successfully activated PDP context" in blob,
        "dns_ok": "DNS configuration is successful" in blob,
        "mqtt_open": "Opened the MQTT client network successfully" in blob,
        "mqtt_connected": "Successfully connected to the server" in blob,
        "upload_ok": "Upload data successfully" in blob,
        "open_fail": "Failed to open the MQTT client network" in blob,
        "send_fail": "Failed to send" in blob,
        "mqtt_cfg_fail": "MQTT configuration failed" in blob,
        "upload_starts": blob.count("*****Upload start"),
        "protocol": next(
            (L.split(":", 1)[-1].strip() for L in lines if "Protocol in Used:" in L),
            "",
        ),
        "csq": next(
            (L.split(":", 1)[-1].strip() for L in lines if "Signal Strength:" in L),
            "",
        ),
    }


def main() -> int:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "railway-mqtt.local.env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "/dev/ttyUSB0"))
    ap.add_argument("--watch", type=float, default=110.0, help="seconds per hypothesis watch")
    ap.add_argument("--udp-port", type=int, default=9999)
    ap.add_argument("--tcp-port", type=int, default=9998)
    ap.add_argument("--wan-ip", default=os.environ.get("WAN_IP", "194.144.202.80"))
    ap.add_argument("--skip-hivemq", action="store_true")
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated hypothesis ids to run (e.g. H2,H3); default=all",
    )
    ap.add_argument("--run-id", default="", help="debug NDJSON runId (default: afk_<stamp>)")
    args = ap.parse_args()
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    pin = resolve_pin(device="ps-cb")
    cfg = load_config()
    if not pin or not cfg.get("MQTT_PASS"):
        print("ERROR: missing PIN or MQTT_PASS", file=sys.stderr)
        return 2

    rail_addr = f"{cfg['MQTT_FALLBACK_IP']},{cfg['MQTT_PORT']}"
    secrets = [pin, cfg["MQTT_PASS"]]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = args.run_id or f"afk_{stamp}"
    logpath = ROOT / "logs" / f"{stamp}_pscb_hypothesis_loop.log"
    logpath.parent.mkdir(exist_ok=True)
    fh = logpath.open("w", encoding="utf-8")

    def out(text: str) -> None:
        safe = text
        for s in secrets:
            if s:
                safe = safe.replace(s, "***")
        row = f"{utc()} {safe}"
        print(row, flush=True)
        fh.write(row + "\n")
        fh.flush()

    udp = UdpSink(args.udp_port)
    tcp = TcpSink(args.tcp_port)
    udp.start()
    tcp.start()
    out(f"SYS sinks UDP:{args.udp_port} TCP:{args.tcp_port} WAN={args.wan_ip}")
    dlog("SETUP", "debug_hypothesis_loop.py:main", "sinks_started", {
        "udp": args.udp_port,
        "tcp": args.tcp_port,
        "wan": args.wan_ip,
        "rail": rail_addr,
        "watch": args.watch,
        "tdc": 90,
    }, run_id=run_id)

    ser = open_serial(args.port, 9600)
    buf = LineBuffer()

    def login(timeout: float = 180.0) -> bool:
        r = unlock(ser, pin, policy="stable", timeout=timeout, on_line=out, on_tx=out)
        out(f"SYS unlock ok={r.ok} phase={r.phase.value}")
        return r.ok

    def apply_cmds(cmds: list[str]) -> None:
        for cmd in cmds:
            shown = cmd
            for s in secrets:
                if s and s in cmd:
                    shown = shown.replace(s, "***")
            out(f"TX {shown}")
            ok, payload = at_cmd(ser, cmd, buf, out, timeout=12.0)
            out(f"RX_ACK {ok} {' | '.join(payload)[:200]}")

    def watch(seconds: float) -> tuple[list[str], dict]:
        lines: list[str] = []
        end = time.time() + seconds

        def on_line(L: str) -> None:
            lines.append(L)
            if any(m in L for m in MARKERS):
                out(f"MARK {L[:160]}")

        while time.time() < end:
            read_for(ser, 1.0, buf, on_line)
        return lines, classify(lines)

    def restore_railway() -> None:
        out("=== RESTORE Railway MQTT ===")
        if not login(160.0):
            out("RESTORE unlock failed")
            return
        apply_cmds(
            [
                "AT+PRO=3,5",
                f"AT+SERVADDR={rail_addr}",
                f"AT+BKDNS=1,0,{rail_addr}",
                f"AT+CLIENT={DEVICE_ID}",
                f"AT+UNAME={cfg['MQTT_USER']}",
                f"AT+PWD={cfg['MQTT_PASS']}",
                f"AT+PUBTOPIC={PUB}",
                f"AT+SUBTOPIC={SUB}",
                "AT+TLSMOD=0,0",
                "AT+MQOS=1",
                "AT+TDC=90",
                "AT+APN=NULL",
            ]
        )
        at_cmd(ser, "ATZ", buf, out, timeout=8.0)
        login(200.0)
        c = read_cfg(ser, buf, out)
        dlog(
            "RESTORE",
            "debug_hypothesis_loop.py:restore",
            "railway_restored",
            {
                "PRO": c.get("PRO"),
                "SERVADDR": c.get("SERVADDR"),
                "APN": c.get("APN"),
                "MQOS": c.get("MQOS"),
                "TDC": c.get("TDC"),
            },
            run_id=run_id,
        )
        out(
            f"RESTORE PRO={c.get('PRO')} SERVADDR={c.get('SERVADDR')} "
            f"APN={c.get('APN')} MQOS={c.get('MQOS')} TDC={c.get('TDC')}"
        )

    experiments: list[tuple[str, str, list[str]]] = [
        (
            "H5",
            "baseline Railway MQOS=1",
            [
                "AT+PRO=3,5",
                f"AT+SERVADDR={rail_addr}",
                f"AT+BKDNS=1,0,{rail_addr}",
                "AT+MQOS=1",
                "AT+TDC=90",
                "AT+APN=NULL",
            ],
        ),
        (
            "H1",
            "Railway MQOS=0",
            [
                "AT+PRO=3,5",
                f"AT+SERVADDR={rail_addr}",
                f"AT+BKDNS=1,0,{rail_addr}",
                "AT+MQOS=0",
                "AT+TDC=90",
                "AT+APN=NULL",
            ],
        ),
        (
            "H2",
            f"UDP JSON to WAN {args.wan_ip}:{args.udp_port}",
            [
                "AT+PRO=2,5",
                f"AT+SERVADDR={args.wan_ip},{args.udp_port}",
                f"AT+BKDNS=1,0,{args.wan_ip},{args.udp_port}",
                "AT+TDC=90",
                "AT+APN=NULL",
            ],
        ),
        (
            "H3",
            f"TCP JSON to WAN {args.wan_ip}:{args.tcp_port}",
            [
                "AT+PRO=4,5",
                f"AT+SERVADDR={args.wan_ip},{args.tcp_port}",
                f"AT+BKDNS=1,0,{args.wan_ip},{args.tcp_port}",
                "AT+TDC=90",
                "AT+APN=NULL",
            ],
        ),
    ]
    if not args.skip_hivemq:
        experiments.append(
            (
                "H4",
                "HiveMQ public control 18.198.118.51:1883 anon",
                [
                    "AT+PRO=3,5",
                    "AT+SERVADDR=18.198.118.51,1883",
                    "AT+BKDNS=1,0,18.198.118.51,1883",
                    "AT+UNAME=NULL",
                    "AT+PWD=NULL",
                    "AT+CLIENT=ps-cb-diag",
                    "AT+PUBTOPIC=dragino/ps-cb/up",
                    "AT+SUBTOPIC=dragino/ps-cb/down",
                    "AT+MQOS=0",
                    "AT+TLSMOD=0,0",
                    "AT+TDC=90",
                    "AT+APN=NULL",
                ],
            )
        )

    if only:
        experiments = [e for e in experiments if e[0] in only]
        if not experiments:
            print(f"ERROR: --only {args.only!r} matched nothing", file=sys.stderr)
            return 2

    results: list[dict] = []
    try:
        out("=== initial unlock ===")
        if not login(200.0):
            dlog("SETUP", "debug_hypothesis_loop.py:main", "unlock_failed", {}, run_id=run_id)
            return 2

        for hid, title, cmds in experiments:
            udp_before = len(udp.packets)
            tcp_before = len(tcp.conns)
            out(f"\n======== {hid}: {title} ========")
            dlog(
                hid,
                "debug_hypothesis_loop.py:exp_start",
                title,
                {"cmds": [c.split("=")[0] for c in cmds]},
                run_id=run_id,
            )
            apply_cmds(cmds)
            at_cmd(ser, "ATZ", buf, out, timeout=8.0)
            lines, summary = watch(args.watch)
            # may need re-login mid-loop for next apply
            if not login(160.0):
                out(f"{hid} re-unlock failed; continuing watch leftover then abort")
                summary["reunlock"] = False
            else:
                summary["reunlock"] = True
            udp_new = udp.packets[udp_before:]
            tcp_new = tcp.conns[tcp_before:]
            summary["udp_packets"] = len(udp_new)
            summary["tcp_conns"] = len(tcp_new)
            summary["hypothesis"] = hid
            summary["title"] = title
            results.append(summary)
            dlog(hid, "debug_hypothesis_loop.py:exp_end", "watch_result", summary, run_id=run_id)
            out(f"SUMMARY {hid} {json.dumps(summary)}")

        restore_railway()
    finally:
        try:
            ser.close()
        except Exception:
            pass
        udp.stop()
        tcp.stop()
        fh.close()

    # Theory scorecard (no secrets)
    verdicts: list[dict] = []
    for s in results:
        hid = s.get("hypothesis", "?")
        if hid in ("H5", "H1"):
            if s.get("mqtt_connected"):
                v, why = "CONFIRMED", "reached Successfully connected"
            elif s.get("open_fail") or s.get("send_fail"):
                v, why = "REJECTED", "still open_fail/send_fail; no CONNACK"
            else:
                v, why = "INCONCLUSIVE", "no clear MQTT success or fail markers"
        elif hid == "H2":
            if s.get("udp_packets", 0) > 0:
                v, why = "CONFIRMED", "UDP datagram reached local sink"
            elif s.get("upload_ok"):
                v, why = "INCONCLUSIVE", "device Upload data successfully but sink got 0 packets"
            else:
                v, why = "REJECTED", "UDP path did not upload"
        elif hid == "H3":
            if s.get("tcp_conns", 0) > 0:
                v, why = "CONFIRMED", "TCP session reached local sink"
            elif s.get("upload_ok"):
                v, why = "INCONCLUSIVE", "device upload_ok but sink got 0 conns"
            else:
                v, why = "REJECTED", "TCP path did not reach sink / no upload_ok"
        elif hid == "H4":
            if s.get("mqtt_connected"):
                v, why = "CONFIRMED", "HiveMQ control CONNECT succeeded"
            elif s.get("open_fail") or s.get("send_fail"):
                v, why = "REJECTED", "public MQTT also fails → not Railway-only"
            else:
                v, why = "INCONCLUSIVE", "no clear HiveMQ outcome"
        else:
            v, why = "INCONCLUSIVE", "unknown hypothesis"
        row = {
            "hypothesis": hid,
            "title": s.get("title"),
            "verdict": v,
            "why": why,
            "evidence": {
                k: s.get(k)
                for k in (
                    "pdp_ok",
                    "dns_ok",
                    "mqtt_open",
                    "mqtt_connected",
                    "upload_ok",
                    "open_fail",
                    "send_fail",
                    "protocol",
                    "csq",
                    "udp_packets",
                    "tcp_conns",
                    "upload_starts",
                )
            },
        }
        verdicts.append(row)
        dlog(hid, "debug_hypothesis_loop.py:verdict", v, row, run_id=run_id)

    scorecard = {
        "sessionId": SESSION,
        "runId": run_id,
        "stamp": stamp,
        "watch_s": args.watch,
        "tdc": 90,
        "rail": rail_addr,
        "wan": f"{args.wan_ip}:{args.udp_port}/{args.tcp_port}",
        "results": results,
        "verdicts": verdicts,
    }
    out_path = ROOT / "logs" / f"{stamp}_pscb_hypothesis_summary.json"
    out_path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(f"SUMMARY_JSON={out_path}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"DEBUG_LOG={DEBUG_LOG}", flush=True)
    dlog("DONE", "debug_hypothesis_loop.py:main", "loop_finished", {"n": len(results), "verdicts": [
        {"h": x["hypothesis"], "v": x["verdict"]} for x in verdicts
    ]}, run_id=run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
