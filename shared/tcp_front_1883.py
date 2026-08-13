#!/usr/bin/env python3
"""Local TCP front on :1883 -> Railway Mosquitto TCP proxy (keeps PS-CB path intact)."""
from __future__ import annotations

import argparse
import os
import select
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs"


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def pipe(a: socket.socket, b: socket.socket, label: str, log) -> None:
    try:
        while True:
            r, _, _ = select.select([a], [], [], 60.0)
            if not r:
                continue
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        try:
            a.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            b.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        log(f"close {label}")


def handle(client: socket.socket, addr, upstream_host: str, upstream_port: int, log) -> None:
    peer = f"{addr[0]}:{addr[1]}"
    log(f"accept {peer}")
    try:
        up = socket.create_connection((upstream_host, upstream_port), timeout=15)
    except OSError as e:
        log(f"upstream_fail {peer} -> {upstream_host}:{upstream_port} {e}")
        client.close()
        return
    log(f"upstream_ok {peer} -> {up.getpeername()}")
    t1 = threading.Thread(target=pipe, args=(client, up, f"{peer}>up", log), daemon=True)
    t2 = threading.Thread(target=pipe, args=(up, client, f"up>{peer}", log), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    client.close()
    up.close()


def main() -> int:
    load_env(ROOT / "railway-mqtt.local.env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument(
        "--upstream-host",
        default=os.environ.get("MQTT_HOST", "altaria.proxy.rlwy.net"),
    )
    ap.add_argument(
        "--upstream-port",
        type=int,
        default=int(os.environ.get("MQTT_PORT", "33239")),
    )
    args = ap.parse_args()

    LOG.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logpath = LOG / f"{stamp}_tcp_front_1883.log"

    def log(msg: str) -> None:
        row = f"{ts()} {msg}"
        print(row, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(row + "\n")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.listen, args.port))
    srv.listen(64)
    log(
        f"LISTEN {args.listen}:{args.port} -> {args.upstream_host}:{args.upstream_port} log={logpath}"
    )
    while True:
        client, addr = srv.accept()
        threading.Thread(
            target=handle,
            args=(client, addr, args.upstream_host, args.upstream_port, log),
            daemon=True,
        ).start()


if __name__ == "__main__":
    raise SystemExit(main())
