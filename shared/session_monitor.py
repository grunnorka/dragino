#!/usr/bin/env python3
"""Fused serial unlock + uplink watch + Railway MQTT subscribe.

Exit codes:
  0  success cycle(s) seen (serial upload_ok and/or MQTT uplink)
  2  unlock / modem / bootloader blocked
  3  real radio fail (no success)
  4  broker unreachable (serial may still be OK)
  5  usage / missing PIN

Example:
  python shared/session_monitor.py --device ps-cb --policy stable --cycles 3
  python shared/session_monitor.py --device ltc2 --policy quiet --cycles 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))

from dragino_uart import (  # noqa: E402
    EXIT_BROKER_FAIL,
    EXIT_OK,
    EXIT_RADIO_FAIL,
    EXIT_UNLOCK_BLOCKED,
    EXIT_USAGE,
    LineBuffer,
    load_dotenv,
    open_serial,
    read_for,
    resolve_pin,
    unlock,
)
from railway_mqtt import load_config  # noqa: E402
from uplink_classify import (  # noqa: E402
    CLASS_RADIO_FAIL,
    CLASS_SUCCESS,
    MARKERS,
    classify,
    note_marker,
)

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("paho-mqtt required: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(EXIT_USAGE)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def device_defaults(device: str) -> Dict[str, str]:
    d = device.lower().replace("_", "-")
    if d in ("ltc2", "ltc2-cb"):
        return {
            "device": "ltc2",
            "policy": "quiet",
            "topic_up": "dragino/ltc2/up",
            "confirm_model": "LTC2-CB",
        }
    return {
        "device": "ps-cb",
        "policy": "stable",
        "topic_up": "dragino/ps-cb/up",
        "confirm_model": "",
    }


class MqttWatcher:
    def __init__(self, topic: str, also_test: bool = False) -> None:
        self.topic = topic
        self.also_test = also_test
        self.messages: List[Dict[str, Any]] = []
        self.connected = False
        self.connect_rc: Optional[int] = None
        self.error: str = ""
        self._client: Any = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        cfg = load_config()
        if not cfg.get("MQTT_PASS"):
            self.error = "MQTT_PASS missing (railway-mqtt.local.env)"
            return False
        host, port = cfg["MQTT_HOST"], int(cfg["MQTT_PORT"])
        client_id = f"session-mon-{os.getpid()}"
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except Exception:
            client = mqtt.Client(client_id=client_id)
        client.username_pw_set(cfg["MQTT_USER"], cfg["MQTT_PASS"])

        def on_connect(c, _u, _f, rc, _p=None):
            code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
            self.connect_rc = code
            self.connected = code == 0
            print(f"{utc()} MQTT CONNECT {code} {host}:{port}", flush=True)
            if code == 0:
                c.subscribe(self.topic)
                print(f"{utc()} MQTT SUB {self.topic}", flush=True)
                if self.also_test:
                    c.subscribe("test/#")

        def on_message(_c, _u, msg):
            body = msg.payload.decode("utf-8", "replace")
            row = {"ts": utc(), "topic": msg.topic, "payload": body[:500]}
            with self._lock:
                self.messages.append(row)
            print(f"{utc()} MQTT {msg.topic} {body[:200]}", flush=True)

        client.on_connect = on_connect
        client.on_message = on_message
        self._client = client
        try:
            client.connect(host, port, 60)
        except Exception as e:
            self.error = str(e)
            print(f"{utc()} MQTT CONNECT_FAIL {e}", flush=True)
            return False
        client.loop_start()
        # brief wait for CONNACK
        for _ in range(50):
            if self.connect_rc is not None:
                break
            time.sleep(0.1)
        return self.connected

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass

    def count_up(self, topic_up: str) -> int:
        with self._lock:
            return sum(1 for m in self.messages if m["topic"] == topic_up or topic_up.endswith("/#"))


def main() -> int:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "railway-mqtt.local.env")

    ap = argparse.ArgumentParser(description="Fused Dragino serial + Railway MQTT session")
    ap.add_argument("--device", choices=["ps-cb", "ltc2"], default="ps-cb")
    ap.add_argument("--policy", choices=["quiet", "burst", "stable"], default="")
    ap.add_argument("--port", default=os.environ.get("DRAGINO_PORT", "COM8"))
    ap.add_argument("--baud", type=int, default=int(os.environ.get("DRAGINO_BAUD", "9600")))
    ap.add_argument("--pin", default="")
    ap.add_argument("--unlock-timeout", type=float, default=240.0)
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--max-seconds", type=float, default=0.0, help="0 = cycles*180 + 120")
    ap.add_argument("--topic", default="dragino/#")
    ap.add_argument("--also-test", action="store_true")
    ap.add_argument("--no-mqtt", action="store_true", help="Serial only")
    ap.add_argument("--skip-unlock", action="store_true", help="Assume already unlocked")
    args = ap.parse_args()

    defs = device_defaults(args.device)
    policy = args.policy or defs["policy"]
    pin = resolve_pin(args.pin, device=args.device)
    if not pin and not args.skip_unlock:
        print("ERROR: missing PIN (DRAGINO_PIN or --pin)", file=sys.stderr)
        return EXIT_USAGE

    max_s = args.max_seconds or (args.cycles * 180 + 120)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpath = logs / f"{stamp}_session_{args.device}.raw.log"
    summary_path = logs / f"{stamp}_session_{args.device}.summary.json"

    def log(tag: str, text: str) -> None:
        safe = text.replace(pin, "***PIN***") if pin else text
        row = f"{utc()} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as fh:
            fh.write(row + "\n")

    summary: Dict[str, Any] = {
        "device": args.device,
        "policy": policy,
        "port": args.port,
        "started": utc(),
        "cycles": [],
        "mqtt": {"enabled": not args.no_mqtt, "messages": []},
    }

    mqtt_w: Optional[MqttWatcher] = None
    broker_ok = True
    if not args.no_mqtt:
        mqtt_w = MqttWatcher(args.topic, also_test=args.also_test)
        broker_ok = mqtt_w.start()
        summary["mqtt"]["connected"] = broker_ok
        summary["mqtt"]["error"] = mqtt_w.error
        summary["mqtt"]["connect_rc"] = mqtt_w.connect_rc

    try:
        ser = open_serial(args.port, args.baud)
    except Exception as e:
        log("SYS", f"PORT_FAIL {e}")
        if mqtt_w:
            mqtt_w.stop()
        return EXIT_USAGE

    log("SYS", f"PORT_OPEN {args.port} policy={policy} log={logpath}")

    confirm = defs["confirm_model"] or None
    if not args.skip_unlock:
        log("SYS", f"UNLOCK start timeout={args.unlock_timeout}s")
        result = unlock(
            ser,
            pin,
            policy=policy,
            timeout=args.unlock_timeout,
            confirm_model=confirm,
            on_line=lambda L: log("RX", L),
            on_tx=lambda L: log("TX", L),
        )
        summary["unlock"] = {
            "ok": result.ok,
            "phase": result.phase.value,
            "hint": result.hint,
            "model_ok": result.model_ok,
        }
        if not result.ok:
            log("SYS", f"UNLOCK_FAIL phase={result.phase.value} {result.hint}")
            print(f"\nBLOCKED: {result.hint}\n", flush=True)
            ser.close()
            if mqtt_w:
                mqtt_w.stop()
            summary["finished"] = utc()
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return result.exit_code
        log("SYS", "UNLOCK_OK Password Correct")
    else:
        summary["unlock"] = {"ok": True, "skipped": True}

    # Monitor cycles
    buf = LineBuffer()
    deadline = time.monotonic() + max_s
    cycle: Optional[Dict[str, Any]] = None
    mqtt_seen_at_cycle_start = 0
    topic_up = defs["topic_up"]

    log("SYS", f"MONITOR cycles={args.cycles} max={max_s}s topic_up={topic_up}")

    def mqtt_new_since(n: int) -> List[Dict[str, Any]]:
        if not mqtt_w:
            return []
        with mqtt_w._lock:
            return [m for m in mqtt_w.messages[n:] if topic_up in m["topic"] or m["topic"].startswith("dragino/")]

    while time.monotonic() < deadline and len(summary["cycles"]) < args.cycles:
        for L in read_for(ser, 1.0, buf, lambda line: log("RX", line)):
            keys = [k for k, rx in MARKERS if rx.search(L)]
            for key in keys:
                if key == "upload_start":
                    if cycle and "end" not in cycle:
                        if mqtt_w:
                            news = mqtt_new_since(mqtt_seen_at_cycle_start)
                            if news:
                                note_marker(cycle, "mqtt_uplink", news[-1]["payload"], news[-1]["ts"])
                        cycle["end"] = {"ts": utc(), "line": "interrupted"}
                        cycle["class"] = classify(cycle)
                        summary["cycles"].append(cycle)
                        log("MARK", f"CYCLE_END interrupted {cycle.get('n')} {cycle['class']}")
                    mqtt_seen_at_cycle_start = len(mqtt_w.messages) if mqtt_w else 0
                    cycle = {"n": len(summary["cycles"]) + 1, "start": utc()}
                    log("MARK", f"CYCLE_START {cycle['n']}")
                    continue
                if key == "upload_end":
                    note_marker(cycle, key, L, utc())
                    if cycle:
                        if mqtt_w:
                            news = mqtt_new_since(mqtt_seen_at_cycle_start)
                            if any(topic_up in m["topic"] for m in news):
                                m = next(m for m in reversed(news) if topic_up in m["topic"])
                                note_marker(cycle, "mqtt_uplink", m["payload"], m["ts"])
                        cycle["end"] = {"ts": utc(), "line": L[:120]}
                        cycle["class"] = classify(cycle)
                        summary["cycles"].append(cycle)
                        log("MARK", f"CYCLE_END {cycle['n']} class={cycle['class']}")
                        cycle = None
                    continue
                note_marker(cycle, key, L, utc())
                if cycle:
                    log("MARK", f"CYCLE{cycle['n']} {key}")

        # MQTT-only cycle boundary if serial quiet but messages arrive
        if mqtt_w and cycle is None and len(summary["cycles"]) < args.cycles:
            with mqtt_w._lock:
                ups = [m for m in mqtt_w.messages if topic_up in m["topic"]]
            if len(ups) > len([c for c in summary["cycles"] if c.get("mqtt_uplink")]):
                # counted via upload_end path normally; skip inventing cycles here
                pass

    if cycle:
        if mqtt_w:
            news = mqtt_new_since(mqtt_seen_at_cycle_start)
            if any(topic_up in m["topic"] for m in news):
                m = next(m for m in reversed(news) if topic_up in m["topic"])
                note_marker(cycle, "mqtt_uplink", m["payload"], m["ts"])
        cycle["end"] = {"ts": utc(), "line": "timeout"}
        cycle["class"] = classify(cycle)
        summary["cycles"].append(cycle)
        log("MARK", f"CYCLE_END timeout {cycle['n']} {cycle['class']}")

    ser.close()
    log("SYS", "PORT_CLOSED")

    if mqtt_w:
        with mqtt_w._lock:
            summary["mqtt"]["messages"] = list(mqtt_w.messages)
            summary["mqtt"]["count"] = len(mqtt_w.messages)
        mqtt_w.stop()

    summary["finished"] = utc()
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    classes = [c.get("class") for c in summary["cycles"]]
    print("=== SUMMARY ===", flush=True)
    for c in summary["cycles"]:
        print(f"CYCLE {c['n']}: {c.get('class')}", flush=True)
    print(f"LOG={logpath}", flush=True)
    print(f"SUMMARY_JSON={summary_path}", flush=True)

    if any(c == CLASS_SUCCESS or (isinstance(c, str) and "false-positive" in c) for c in classes):
        # false-positive teardown still means uplink worked
        if not broker_ok and not args.no_mqtt:
            return EXIT_BROKER_FAIL
        return EXIT_OK
    if any(c == CLASS_RADIO_FAIL for c in classes):
        return EXIT_RADIO_FAIL
    if not broker_ok and not args.no_mqtt and summary["cycles"]:
        return EXIT_BROKER_FAIL
    if not summary["cycles"]:
        return EXIT_RADIO_FAIL if broker_ok or args.no_mqtt else EXIT_BROKER_FAIL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
