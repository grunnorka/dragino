"""Railway Mosquitto broker settings + Dragino AT snippets (print only).

Loads from environment / railway-mqtt.local.env / .env.
Does not talk to sensors.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Deployed defaults (override via env / local env file)
DEFAULTS = {
    "MQTT_HOST": "altaria.proxy.rlwy.net",
    "MQTT_PORT": "33239",
    "MQTT_USER": "dragino",
    "MQTT_PASS": "",  # set in railway-mqtt.local.env
    "MQTT_FALLBACK_IP": "66.33.22.220",
    "MQTT_APP_PORT": "1883",
}

RAILWAY = {
    "project": "dragino-mqtt",
    "project_id": "6275a0e4-fa40-4b5b-ae8c-67180378148e",
    "service": "mqtt",
    "service_id": "73332af7-a679-417d-b884-f4f76d6971be",
    "environment": "production",
    "environment_id": "e6115556-b83e-4aac-9327-4efa654e93b3",
    "tcp_proxy": "altaria.proxy.rlwy.net:33239",
}


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def load_config() -> dict[str, str]:
    _load_env_file(ROOT / "railway-mqtt.local.env")
    _load_env_file(ROOT / ".env")
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in os.environ and os.environ[key]:
            cfg[key] = os.environ[key]
    return cfg


def servaddr(cfg: dict[str, str], *, use_ip: bool = False) -> str:
    host = cfg["MQTT_FALLBACK_IP"] if use_ip else cfg["MQTT_HOST"]
    return f"{host},{cfg['MQTT_PORT']}"


def at_commands(
    cfg: dict[str, str],
    *,
    device_id: str = "ps-cb",
    tdc: int = 180,
    use_ip: bool = False,
) -> list[str]:
    """JSON MQTT profile (PRO=3,5) — avoids ThingsBoard HiveMQ rewrite."""
    addr = servaddr(cfg, use_ip=use_ip)
    pub = f"dragino/{device_id}/up"
    sub = f"dragino/{device_id}/down"
    return [
        "AT+PRO=3,5",
        f"AT+SERVADDR={addr}",
        f"AT+UNAME={cfg['MQTT_USER']}",
        f"AT+PWD={cfg['MQTT_PASS']}",
        f"AT+PUBTOPIC={pub}",
        f"AT+SUBTOPIC={sub}",
        f"AT+CLIENT={device_id}",
        "AT+MQOS=1",
        "AT+TLSMOD=0,0",
        f"AT+BKDNS=1,0,{cfg['MQTT_FALLBACK_IP']},{cfg['MQTT_PORT']}",
        f"AT+TDC={tdc}",
        # Re-assert after PRO side effects
        f"AT+SERVADDR={addr}",
        f"AT+UNAME={cfg['MQTT_USER']}",
        f"AT+PWD={cfg['MQTT_PASS']}",
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Print Railway MQTT config / Dragino AT set")
    ap.add_argument("--device-id", default="ps-cb")
    ap.add_argument("--tdc", type=int, default=180)
    ap.add_argument("--use-ip", action="store_true", help="SERVADDR uses fallback IP")
    ap.add_argument("--at", action="store_true", help="Print AT commands only")
    args = ap.parse_args()
    cfg = load_config()

    if args.at:
        for line in at_commands(cfg, device_id=args.device_id, tdc=args.tdc, use_ip=args.use_ip):
            print(line)
        return

    print("Railway project:", RAILWAY["project"], RAILWAY["project_id"])
    print("Service:", RAILWAY["service"], RAILWAY["service_id"])
    print("TCP proxy:", f"{cfg['MQTT_HOST']}:{cfg['MQTT_PORT']}")
    print("Fallback IP:", cfg["MQTT_FALLBACK_IP"])
    print("User:", cfg["MQTT_USER"])
    print("Pass:", cfg["MQTT_PASS"])
    print("TLS: off")
    print()
    print("--- Dragino AT (JSON MQTT PRO=3,5) ---")
    for line in at_commands(cfg, device_id=args.device_id, tdc=args.tdc, use_ip=args.use_ip):
        print(line)


if __name__ == "__main__":
    main()
