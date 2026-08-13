#!/usr/bin/env python3
"""Retry EMQX Cloud with CA cert upload (BG95 AT+QFUPL)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent
CA_PATH = Path(r"c:\Users\Arnor\Downloads\emqxsl-ca.crt")

EMQX_HOST = "q933922f.ala.eu-central-1.emqxsl.com"
EMQX_PORT = 8883
EMQX_USER = "dragino"
EMQX_PASS = "dragino"
PUB = "dragino/ps-cb/up"
SUB = "dragino/ps-cb/down"

TB = "167.235.104.181,1883"
TB_TOKEN = "7donD0lgPwI5aJcS83dS"
TARGET_CYCLES = 2
LISTEN_S = 480


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def prepare_ca(path: Path) -> tuple[str, int, list[str]]:
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    text = raw.decode("ascii")
    if not text.endswith("\n"):
        text += "\n"
    # Dragino: size = file chars (LF) + number of line breaks (extra CR when sending CRLF)
    size = len(text) + text.count("\n")
    lines = text.splitlines()  # no trailing empties from final \n alone beyond content
    # Keep PEM lines as stored (splitlines drops the final empty after trailing \n)
    return text, size, lines


def main() -> None:
    load_dotenv(ROOT / ".env")
    pin = os.environ["DRAGINO_PIN"].strip()
    ca_text, ca_size, ca_lines = prepare_ca(CA_PATH)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = ROOT / "logs" / f"{stamp}_emqx_ca_retry.raw.log"
    ser = serial.Serial("COM8", 9600, timeout=0.2)
    print(f"Opened COM8; log={logpath}; ca_size={ca_size} lines={len(ca_lines)}", flush=True)
    buf = b""

    def log(tag: str, s: str) -> None:
        safe = s.replace(EMQX_PASS, "***")
        if len(safe) > 200 and ("BEGIN CERTIFICATE" in safe or "END CERTIFICATE" in safe or safe.startswith("MII")):
            safe = safe[:40] + f"...({len(s)} chars)"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        row = f"{ts} {tag} {safe}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    def read_lines(seconds: float) -> list[str]:
        nonlocal buf
        end = time.time() + seconds
        out: list[str] = []
        while time.time() < end:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    if text:
                        log("RX", text)
                        out.append(text)
            else:
                time.sleep(0.02)
        return out

    def send(cmd: str, wait: float = 2.0) -> list[str]:
        log("TX", cmd)
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def send_raw_line(line: str, wait: float = 0.8) -> list[str]:
        log("TX", line)
        ser.write((line + "\r\n").encode("ascii"))
        ser.flush()
        return read_lines(wait)

    def unlock(n: int = 12) -> bool:
        for _ in range(n):
            if any("Password Correct" in L for L in send(pin, 2.0)):
                return True
            send(f"AT+PIN={pin}", 1.2)
            time.sleep(0.3)
        return False

    def wait_idle(max_s: float = 180.0) -> bool:
        """Wait until not in active upload (power-off or quiet after End of upload)."""
        log("TEST", f"WAIT_IDLE up to {max_s}s")
        deadline = time.time() + max_s
        saw_upload = False
        quiet_since = None
        while time.time() < deadline:
            lines = read_lines(1.0)
            for L in lines:
                if "Upload start" in L:
                    saw_upload = True
                    quiet_since = None
                if "End of upload" in L or "power-off successful" in L:
                    quiet_since = time.time()
            if quiet_since and (time.time() - quiet_since) >= 8.0:
                log("TEST", "IDLE_AFTER_UPLOAD")
                return True
            if not lines and not saw_upload:
                # already quiet
                more = read_lines(5.0)
                if not more:
                    log("TEST", "IDLE_QUIET")
                    return True
                for L in more:
                    if "Upload start" in L:
                        saw_upload = True
        log("TEST", "IDLE_TIMEOUT_CONTINUE")
        return False

    def query() -> dict[str, str]:
        info: dict[str, str] = {}
        for key, cmd in [
            ("servaddr", "AT+SERVADDR=?"),
            ("bkdns", "AT+BKDNS=?"),
            ("pro", "AT+PRO=?"),
            ("tdc", "AT+TDC=?"),
            ("pub", "AT+PUBTOPIC=?"),
            ("sub", "AT+SUBTOPIC=?"),
            ("uname", "AT+UNAME=?"),
            ("tls", "AT+TLSMOD=?"),
        ]:
            for L in send(cmd, 1.8):
                t = L.strip()
                if not t or t == "OK" or t.startswith("AT+") or t.startswith("["):
                    continue
                info[key] = t
                break
        return info

    def upload_ca() -> bool:
        log("TEST", f"CA_UPLOAD size={ca_size} lines={len(ca_lines)}")
        if not unlock():
            log("TEST", "UNLOCK_FAIL_BEFORE_CA")
            return False
        send("AT+CERTMOD", 2.5)
        # delete old if present (ERROR ok)
        send('AT+QFDEL="cacert.pem"', 2.5)
        lines = send(f'AT+QFUPL="cacert.pem",{ca_size},100', 5.0)
        if not any("CONNECT" in L for L in lines):
            # maybe still waiting
            lines += read_lines(5.0)
        if not any("CONNECT" in L for L in lines):
            log("TEST", "NO_CONNECT_FOR_QFUPL")
            send("AT+CERTMOD", 2.0)  # try exit
            return False
        for i, line in enumerate(ca_lines):
            send_raw_line(line, wait=1.0 if i < 3 or i >= len(ca_lines) - 2 else 0.5)
        # wait for +QFUPL
        tail = read_lines(8.0)
        ok = any("+QFUPL:" in L for L in tail) or any("OK" in L for L in tail)
        send("AT+CERTMOD", 2.5)  # exit cert mode
        log("TEST", f"CA_UPLOAD_DONE ok={ok}")
        return ok

    def apply_emqx() -> None:
        unlock()
        send("AT+PRO=3,5", 2.5)
        # Server authentication only (CA present, no client cert)
        send("AT+TLSMOD=1,1", 2.0)
        send(f"AT+SERVADDR={EMQX_HOST},{EMQX_PORT}", 2.5)
        send("AT+BKDNS=1,0", 2.0)
        send(f"AT+UNAME={EMQX_USER}", 1.5)
        send(f"AT+PWD={EMQX_PASS}", 1.5)
        send(f"AT+PUBTOPIC={PUB}", 1.5)
        send(f"AT+SUBTOPIC={SUB}", 1.5)
        send("AT+CLIENT=null", 1.5)
        send("AT+MQOS=1", 1.5)
        send("AT+TDC=180", 1.5)
        send("AT+CDP=0", 2.0)
        # Re-assert after PRO side effects / lock
        unlock()
        send(f"AT+SERVADDR={EMQX_HOST},{EMQX_PORT}", 2.5)
        send("AT+BKDNS=1,0", 2.0)
        send("AT+TLSMOD=1,1", 2.0)
        send(f"AT+PUBTOPIC={PUB}", 1.5)
        send(f"AT+SUBTOPIC={SUB}", 1.5)
        send(f"AT+UNAME={EMQX_USER}", 1.5)
        send(f"AT+PWD={EMQX_PASS}", 1.5)

    def restore_tb() -> None:
        unlock()
        send("AT+PRO=3,3", 2.5)
        send("AT+TLSMOD=0,0", 2.0)
        send(f"AT+SERVADDR={TB}", 2.0)
        send(f"AT+BKDNS=1,0,{TB}", 2.5)
        send(f"AT+UNAME={TB_TOKEN}", 1.5)
        send("AT+PWD=NULL", 1.5)
        send("AT+PUBTOPIC=v1/devices/me/telemetry", 1.5)
        send("AT+SUBTOPIC=v1/devices/me/attributes", 1.5)
        send("AT+TDC=180", 1.5)
        unlock()
        send(f"AT+SERVADDR={TB}", 2.0)
        send(f"AT+BKDNS=1,0,{TB}", 2.0)

    read_lines(1.0)
    wait_idle(200.0)
    ca_ok = upload_ca()
    log("TEST", f"ca_ok={ca_ok}")
    apply_emqx()
    cfg = query()
    log("TEST", f"CFG {cfg}")

    cycles: list[list[str]] = []
    current: list[str] = []
    phase = "idle"
    deadline = time.time() + LISTEN_S
    log("TEST", f"LISTEN {TARGET_CYCLES} EMQX+CA cycles, up to {LISTEN_S}s")

    while time.time() < deadline and len(cycles) < TARGET_CYCLES:
        for L in read_lines(1.0):
            if "Upload start" in L:
                phase = "upload"
                current = [L]
                log("MARK", f"CYCLE_{len(cycles)+1}_BEGIN")
            elif phase == "upload":
                current.append(L)
                if "End of upload" in L:
                    for T in read_lines(20.0):
                        current.append(T)
                        if "Upload start" in T:
                            cycles.append(current[:-1])
                            log("MARK", f"CYCLE_{len(cycles)}_END")
                            current = [T]
                            log("MARK", f"CYCLE_{len(cycles)+1}_BEGIN")
                            break
                    else:
                        cycles.append(current)
                        log("MARK", f"CYCLE_{len(cycles)}_END")
                        phase = "idle"
                        current = []
                        if len(cycles) >= TARGET_CYCLES:
                            deadline = 0
                            break

    summaries = []
    for i, cyc in enumerate(cycles, 1):
        s = {
            "n": i,
            "failed": sum(1 for L in cyc if "Failed to send" in L),
            "success": sum(1 for L in cyc if "Upload data successfully" in L),
            "subscribe_ok": sum(1 for L in cyc if "Subscribe to topic successfully" in L),
            "mqtt_open": sum(1 for L in cyc if "Opened the MQTT" in L),
            "connected": sum(1 for L in cyc if "Successfully connected to the server" in L),
            "domain": [L for L in cyc if "Domain IP" in L][:3],
            "ssl": [L for L in cyc if "SSL" in L or "TLS" in L or "certificate" in L.lower()][:6],
            "fail_other": [L for L in cyc if "Fail" in L or "fail" in L or "ERROR" in L][:8],
        }
        summaries.append(s)
        log("RESULT", f"cycle{i}={s}")
        for L in cyc:
            if any(
                k in L
                for k in (
                    "Upload",
                    "Failed",
                    "TCP",
                    "MQTT",
                    "Domain",
                    "Subscribe",
                    "connected",
                    "Resolving",
                    "End of",
                    "TLS",
                    "SSL",
                    "emqx",
                    "8883",
                    "QFUPL",
                    "cert",
                )
            ):
                log(f"C{i}", L)

    log("TEST", "RESTORE_PRIVATE_TB")
    restore_tb()
    final = query()
    log("TEST", f"FINAL_CFG {final}")

    print("=== SUMMARY ===", flush=True)
    print(f"ca_ok={ca_ok} ca_size={ca_size}", flush=True)
    print(f"CFG={cfg}", flush=True)
    print(f"cycles_captured={len(cycles)}", flush=True)
    for s in summaries:
        print(s, flush=True)
    print(f"RESTORED={final}", flush=True)
    ser.close()
    print(f"LOG={logpath}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
