"""Classify Dragino uplink cycles from serial markers (+ optional MQTT).

Rules match PS-CB-NA observe_uplink_cycles:
  - upload_ok + later Failed to send / TCP close = false-positive teardown
  - CSQ=99 or fail without upload_ok = real radio fail
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# (key, regex) — applied case-insensitively via re.I
MARKERS: List[Tuple[str, re.Pattern[str]]] = [
    ("signal_strength", re.compile(r"signal\s*strength|CSQ[=:\s]|\"signal\"\s*:", re.I)),
    (
        "network_attach",
        re.compile(
            r"network\s+(connected|attach)|register\s+success|NB[- ]?IoT.*attach|"
            r"Searching for network|Network connected",
            re.I,
        ),
    ),
    ("mqtt_connect", re.compile(r"successfully\s+connected|MQTT\s+connect|mqtt\s+connected", re.I)),
    ("upload_ok", re.compile(r"Upload data successfully", re.I)),
    ("subscribe_ok", re.compile(r"Subscribe\s+(OK|success)|subscribe.*success", re.I)),
    ("failed_send", re.compile(r"Failed to send", re.I)),
    ("failed_tcp_close", re.compile(r"Failed to close TCP", re.I)),
    ("csq_99", re.compile(r"CSQ[=:\s]+99\b|CSQ:\s*99\b", re.I)),
    ("upload_start", re.compile(r"\*+Start of upload\*+|Start of upload", re.I)),
    ("upload_end", re.compile(r"\*+End of upload\*+|End of upload|power-off successful", re.I)),
]

CLASS_SUCCESS = "real success"
CLASS_FALSE_POSITIVE = "false-positive teardown after success"
CLASS_RADIO_FAIL = "real radio fail"
CLASS_INCOMPLETE = "incomplete / unclear"


def classify(cycle: Dict[str, Any], *, mqtt_msg: bool = False) -> str:
    """Classify one cycle dict keyed by MARKERS (+ optional mqtt_msg flag)."""
    ok = cycle.get("upload_ok") is not None or mqtt_msg or cycle.get("mqtt_uplink") is not None
    fail = cycle.get("failed_send") is not None
    tcp = cycle.get("failed_tcp_close") is not None
    csq99 = cycle.get("csq_99") is not None
    mqtt = cycle.get("mqtt_connect") is not None or mqtt_msg or cycle.get("mqtt_uplink") is not None

    if cycle.get("upload_ok") is not None and (fail or tcp):
        return CLASS_FALSE_POSITIVE
    if cycle.get("upload_ok") is not None and mqtt:
        return CLASS_SUCCESS
    if cycle.get("upload_ok") is not None:
        return CLASS_SUCCESS
    if mqtt_msg or cycle.get("mqtt_uplink") is not None:
        return CLASS_SUCCESS
    if csq99 or (not mqtt and fail) or (not ok and not mqtt):
        if csq99 or not mqtt:
            return CLASS_RADIO_FAIL
    if fail and not ok:
        return CLASS_RADIO_FAIL
    return CLASS_INCOMPLETE


def match_markers(line: str) -> List[str]:
    """Return marker keys that match this RX line."""
    return [key for key, rx in MARKERS if rx.search(line)]


def is_upload_success_line(line: str) -> bool:
    return bool(re.search(r"Upload data successfully", line, re.I))


def is_failed_send_line(line: str) -> bool:
    return bool(re.search(r"Failed to send", line, re.I))


def note_marker(cycle: Optional[Dict[str, Any]], key: str, line: str, ts: str) -> None:
    if cycle is None:
        return
    if cycle.get(key) is None:
        cycle[key] = {"ts": ts, "line": line[:200]}
