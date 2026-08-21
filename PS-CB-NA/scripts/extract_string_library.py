#!/usr/bin/env python3
"""Build a reverse-engineering string library for the PS-CB-NA sensor.

Two sources are combined:

1. The firmware image string table (ground truth: every message the firmware can
   ever emit, including the printf format specifiers).
2. The captured serial logs in logs/ (proves which messages were actually seen,
   and with which concrete values).

Log lines are normalised by stripping the harness timestamp, the RX/TX/SYS tag
and the [uptime_ms] prefix, so "2026-08-16T19:36:50.344Z RX [67808]DNS
configuration is successful" reduces to "DNS configuration is successful".

Usage:
    python3 PS-CB-NA/scripts/extract_string_library.py            # write markdown
    python3 PS-CB-NA/scripts/extract_string_library.py --plain    # bare list to stdout
"""

from __future__ import annotations

import argparse
import collections
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_FW = os.path.join(ROOT, "PS-CB-NA/firmware/PS-CB-NA_v1.2.1.bin")
BOOT_FW = os.path.join(ROOT, "PS-CB-NA/firmware/DRAGINO_NB_bootloader_v1.3.bin")
LOG_GLOB = os.path.join(ROOT, "logs/*.log")
OUT_MD = os.path.join(ROOT, "PS-CB-NA/STRING_LIBRARY.md")

# ---------------------------------------------------------------- firmware side

# Compiler-glued neighbours: these strings begin mid-way through an unrelated
# constant, so the leading bytes are not part of the message.
GLUED_PREFIX = {
    "PClose OTA upgrade": "Close OTA upgrade",
    "zD water_deep:%.3f": "water_deep:%.3f",
    "zD,\"%d\":[%.3f,%.3f,%.2f,\"%d-%02d-%02dT%02d:%02d:%02dZ\"]":
        ",\"%d\":[%.3f,%.3f,%.2f,\"%d-%02d-%02dT%02d:%02d:%02dZ\"]",
    "PAT+PWRM2": "AT+PWRM2",
}

# Instruction bytes that happen to decode as printable ASCII.
JUNK = {
    "+Mnk&Cnc", "%d<xk,*", "hIh:F", "oJ :R~", "Ff:1", "Ff:", "gI:", "%fp",
    "%dH.F", "F :", "&%xd", "%xd", ".A%.8x", "%0X", "%.2x", "%.4x", "%02x",
    "%.8x", "%d,\"", "0123456789abcdef", "0123456789ABCDEF",
}

PRINTF = re.compile(r"%[-+ #0]*[0-9]*\.?[0-9]*(?:ll|l|h)?[diuoxXfeEgGcsp%]")


def firmware_strings(path: str, minlen: int = 3) -> list[str]:
    """Pull printable runs out of a raw firmware image, keeping message-like ones."""
    with open(path, "rb") as fh:
        blob = fh.read()

    out: list[str] = []
    for run in re.findall(rb"[\x20-\x7e]{%d,}" % minlen, blob):
        s = run.decode("ascii").strip()
        # A trailing/leading constant can be glued on; realign on the [%u] tag.
        tag = s.find("[%u]")
        if tag > 0:
            s = s[tag:]
        s = GLUED_PREFIX.get(s, s).strip()
        if not s or s in JUNK:
            continue
        if _is_message(s):
            out.append(s)
    return list(dict.fromkeys(out))


def _is_message(s: str) -> bool:
    if s.startswith(("AT", "{\"", "[%u]", "+QMTRECV")):
        return True
    if len(s) < 5:
        return s in {"OK", "ERROR", "NULL"}
    words = re.findall(r"[A-Za-z]{3,}", s)
    has_fmt = bool(PRINTF.search(s))
    if has_fmt and (words or "*****" in s):
        return True
    if len(words) < 2:
        return False
    if not re.search(r"[aeiouAEIOU]", s):
        return False
    letters = sum(c.isalpha() or c.isspace() for c in s)
    return letters / len(s) >= 0.72


def literal_len(tmpl: str) -> int:
    """Characters of fixed text in a template, ignoring the [%u] uptime tag."""
    body = tmpl[4:] if tmpl.startswith("[%u]") else tmpl
    return len(PRINTF.sub("", body))


def template_regex(tmpl: str) -> re.Pattern:
    """Turn a printf template into a regex that matches a concrete log line."""
    body = tmpl[4:] if tmpl.startswith("[%u]") else tmpl
    parts, last = [], 0
    for m in PRINTF.finditer(body):
        parts.append(re.escape(body[last:m.start()]))
        spec = m.group(0)
        if spec == "%%":
            parts.append("%")
        elif spec.endswith(("d", "i", "u")):
            parts.append(r"-?\d+")
        elif spec.endswith(("f", "e", "E", "g", "G")):
            parts.append(r"-?\d+(?:\.\d+)?")
        elif spec.endswith(("x", "X", "o")):
            parts.append(r"[0-9a-fA-F]+")
        elif spec == "%c":
            parts.append(".")
        else:  # %s, %p
            parts.append(r".*?")
        last = m.end()
    parts.append(re.escape(body[last:]))
    return re.compile(r"^\s*" + "".join(parts) + r"\s*$")


# --------------------------------------------------------------------- log side

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*")
TAG = re.compile(r"^(RX_ACK|RX|TX|SYS|MARK|wake-ish:|wake:)\s*")
UPTIME = re.compile(r"^\[(\d+)\]")


def log_messages() -> collections.Counter:
    """Distinct device-side lines seen in the captured serial logs, with counts."""
    seen: collections.Counter = collections.Counter()
    for path in sorted(glob.glob(LOG_GLOB)):
        with open(path, errors="replace") as fh:
            for line in fh:
                s = TS.sub("", line.rstrip("\r\n"))
                s = TAG.sub("", s)
                s = UPTIME.sub("", s).strip()
                if s:
                    seen[s] += 1
    return seen


# ------------------------------------------------------------------ categories

CATEGORIES: list[tuple[str, list[str]]] = [
    # JSON payloads first: they mention IMEI/MQTT/OTA and would otherwise be
    # scattered across the protocol buckets.
    ("JSON uplink and status payloads", [r"^\{", r"^,\"%d\"", r"^\"%d/"]),
    ("Downlink JSON config keys", [r"^AT\+[A-Z0-9]+\":\"$"]),
    ("Boot, module power and reset", [
        r"NB module is initializing", r"NBIOT (did not respond|has responded)",
        r"Echo mode", r"Closing NB module", r"NB module power-off",
        r"Restart the module", r"No response when shutting down", r"power (on|off)$",
        r"reboot error:", r"awakened module", r"^AT\+PWRM2$", r"^AT\+QSW",
        r"^AT\+CFUN", r"Image Version", r"^FW check", r"Hardware Not Support",
        r"The device is busy", r"^Please wait for the erase",
    ]),
    ("Modem identity and radio", [
        r"manufacturer model", r"Model information:BG95", r"IMEI", r"IMSI",
        r"Frequency band", r"Network Category", r"Signal Strength",
        r"query network information", r"Network Information", r"Failed to set COPS",
        r"RF function",
        r"^AT\+(CGMM|CGSN|CIMI|CSQ|CGATT|COPS)", r"^AT\+QCFG", r"^AT\+QNWINFO",
        r"^AT\+QCOPS", r"^AT\+QBAND",
    ]),
    ("Time and clock", [
        r"Failed to get time", r"^AT\+CCLK", r"^AT\+TIMESTAMP", r"CLOCK:",
        r"Set after calibration time", r"needs to be greater than",
        r"^AT\+CLOCKLOG",
    ]),
    ("APN, PDP context and data format", [
        r"data format", r"[Ss]et APN", r"TCP/IP context", r"PDP context",
        r"^AT\+(CGDCONT|QICSGP|QIACT|QIDEACT|QICFG)", r"^AT\+APN",
    ]),
    ("DNS and NTP", [
        r"DNS", r"Domain (name|IP)", r"Resolving domain", r"NTP",
        r"^AT\+(QIDNSGIP|QIDNSCFG|QNTP)",
    ]),
    ("TLS / SSL / certificates", [
        r"SSL", r"certificate", r"private key", r"authentication",
        r"Name Indication", r"certificate mode", r"^AT\+QSSLCFG",
    ]),
    ("MQTT", [
        r"MQTT", r"subscribe to topic|Subscribe to topic",
        r"TCP connection is closed|Failed to close TCP connection",
        r"connected to the server|Failed to connect to server", r"Failed to Set PUB",
        r"^AT\+QMT", r"v1/devices/me", r"channels/", r"mqtt-integration-tutorial",
    ]),
    ("UDP", [r"^\[%u\]UDP", r"UDP parameter", r"Datagram is sent by RF",
             r"UDP SERVICE"]),
    ("TCP / socket service", [
        r"TCP parameter", r"Socket Service", r"Failed to upload data",
        r"Close the port|Failed to close the port", r"^AT\+QI(CLOSE|OPEN|RD|SEND)",
        r"^SEND FAIL$",
    ]),
    ("CoAP", [r"CoAP", r"COAP", r"^AT\+QCOAP", r"^AT\+URI\d"]),
    ("Upload cycle", [
        r"Upload start", r"End of upload", r"Send complete", r"Failed to send",
        r"Upload data successfully", r"uploading 100", r"Protocol in Used",
    ]),
    ("Sensor readings and payload fields", [
        r"^\[%u\]BAT:", r"^\[%u\]IN\d:", r"GPIO_EXTI", r"IDC_Input", r"VDC_Input",
        r"^\[%u\]Battery:", r"^\[%u\]IDC :", r"CLOCK:", r"water_deep",
        r"differential_pressure", r"^pressure:", r"IDC_(INC|DEC|LOW|HIGH)",
        r"VDC_(INC|DEC|LOW|HIGH)", r"mA,", r"idc_input=", r"field1=",
        r"^PS-CB sensor detected",
    ]),
    ("GNSS / GPS", [r"GNSS", r"location information", r"Searching for location",
                    r"latitude", r"^AT\+QGPS"]),
    ("Downlink handling", [
        r"downlink data", r"Received downlink", r"Downklink_Ack", r"No data in buffer",
        r"Confirm ACK", r"Event:Status", r"^\{\"Config\"", r"Reset the device after",
        r"Downstream parameter error", r"retrieve data completed", r"No data retrieved",
        r"Retrieve", r"Clear all stored sensor data", r"Stop Tx events",
    ]),
    ("OTA upgrade", [
        r"OTA", r"ota server", r"firmware information", r"request download",
        r"Downloading %d", r"fw_state", r"fw_title|fw_version|fw_checksum",
        r"server_fw_", r"Consistent firmware|Inconsistent firmware",
        r"CRC error", r"v2/fw/request", r"ota update", r"[Rr]equest download",
    ]),
    ("Console password and access", [
        r"Password (Correct|Incorrect|timeout)", r"^AT\+PWORD", r"Debug mode",
    ]),
    ("AT command layer: errors and notices", [
        r"^AT_", r"^ERROR$", r"^OK$", r"^NULL$", r"Attention:Take effect",
        r"^NOTE:", r"needs to be greater than", r"^AT\+<CMD>",
    ]),
    ("AT command setters (echoed on write)", [r"^AT\+[A-Z0-9]+=$"]),
    ("AT command help text", [r"^AT[A-Z+].*:"]),
    ("AT command names (AT+CFG / help index)", [r"^AT[A-Z+0-9]+$"]),
    ("Flash and storage", [r"Erase operation", r"Write operation", r"write_error_flag"]),
    ("Manual banner", [r"SensorManual", r"^Protocol in Used"]),
    ("Generic primitives and bare format strings", [
        r"^AT\??$", r"^\[%u\](%s)?$", r"^AT\+NAME%s$", r"^\+QMTRECV$", r"^%",
    ]),
]


def categorise(strings: list[str]) -> "collections.OrderedDict[str, list[str]]":
    buckets: collections.OrderedDict[str, list[str]] = collections.OrderedDict(
        (name, []) for name, _ in CATEGORIES
    )
    buckets["Uncategorised"] = []
    for s in strings:
        for name, pats in CATEGORIES:
            if any(re.search(p, s) for p in pats):
                buckets[name].append(s)
                break
        else:
            buckets["Uncategorised"].append(s)
    return collections.OrderedDict((k, v) for k, v in buckets.items() if v)


# ----------------------------------------------------------------------- output

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", action="store_true",
                    help="print the bare string list to stdout instead of writing markdown")
    ap.add_argument("--observed-only", action="store_true",
                    help="restrict output to messages actually seen in logs/")
    args = ap.parse_args()

    app = firmware_strings(APP_FW)
    boot = firmware_strings(BOOT_FW)
    logs = log_messages()

    # Match every log line against the firmware templates. Templates are tried
    # most-specific first so that a near-wildcard like "[%u]%s" cannot claim a
    # line that a concrete message explains.
    matched: dict[str, list[str]] = {t: [] for t in app + boot}
    regexes = sorted(
        ((t, template_regex(t)) for t in app + boot if literal_len(t) >= 3),
        key=lambda tr: literal_len(tr[0]),
        reverse=True,
    )
    for line in logs:
        for tmpl, rx in regexes:
            if rx.match(line):
                matched[tmpl].append(line)
                break

    strings = [t for t in app if not args.observed_only or matched[t]]

    if args.plain:
        for s in strings:
            print(s[4:] if s.startswith("[%u]") else s)
        return 0

    buckets = categorise(strings)
    n_obs = sum(1 for t in app if matched[t])

    lines = [
        "# PS-CB-NA string library",
        "",
        "Every log / debug / response string the sensor can emit, extracted from the",
        "firmware image string table and cross-referenced against the captured serial",
        "logs in `logs/`.",
        "",
        f"- Source firmware: `{os.path.relpath(APP_FW, ROOT)}` (Image Version v1.2.1, NB-IoT stack D-BG95-004)",
        f"- Source bootloader: `{os.path.relpath(BOOT_FW, ROOT)}`",
        f"- Log files scanned: {len(glob.glob(LOG_GLOB))}",
        f"- Firmware strings recovered: {len(app)} ({n_obs} confirmed in logs)",
        "",
        "`%u` is the millisecond uptime the firmware prints as `[%u]` at the start of a",
        "line. `%s` is a string, `%d` an integer, `%.3f` a float. Regenerate this file",
        "with `python3 PS-CB-NA/scripts/extract_string_library.py`.",
        "",
        "A leading `*` marks a string confirmed in the captured logs.",
        "",
    ]

    for name, items in buckets.items():
        lines.append(f"## {name}")
        lines.append("")
        lines.append("```")
        for s in items:
            mark = "*" if matched[s] else " "
            lines.append(f"{mark} {s}")
        lines.append("```")
        lines.append("")

    lines += ["## Bootloader (DRAGINO NB bootloader v1.3)", "", "```"]
    for s in boot:
        lines.append(f"{'*' if matched[s] else ' '} {s}")
    lines += ["```", ""]

    # Anything in the logs the firmware table does not explain.
    device_like = re.compile(
        r"^(AT[+A-Z]|OK$|ERROR$|NULL$|\+Q|Attention:|NOTE:|[A-Z][A-Za-z ]+ *= )")
    # Annotations added by our own test scripts, not emitted by the sensor.
    harness = re.compile(r" -> | WANT | now = |watching|= '|\*\*\*PIN\*\*\*|NO ACK")
    unexplained = [
        (l, n) for l, n in logs.most_common()
        if not any(rx.match(l) for _, rx in regexes)
        and device_like.match(l) and not harness.search(l)
    ]
    if unexplained:
        lines += [
            "## Seen in logs but not in the firmware string table",
            "",
            "Runtime-composed output (`AT+CFG` dump lines, command echoes with values",
            "substituted) plus modem responses that come from the BG95 rather than the",
            "STM32 application.",
            "",
            "```",
        ]
        for l, n in unexplained:
            lines.append(f"{n:5d}x  {l}")
        lines += ["```", ""]

    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {OUT_MD}: {len(app)} firmware strings, {n_obs} confirmed in logs, "
          f"{len(unexplained)} log-only strings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
