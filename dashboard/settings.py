"""Environment settings for ingest and web services."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def _load_local_env() -> None:
    _load_env_file(ROOT / "railway-mqtt.local.env")
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / "dashboard.local.env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_pass: str
    mqtt_topic: str
    device_ids: tuple[str, ...]
    stale_after_hours: int
    basic_auth_user: str
    basic_auth_password: str
    messages_per_device: int
    refresh_seconds: int


def load_settings(*, ingest_defaults: bool = False) -> Settings:
    """Load settings from env.

    When ingest_defaults=True, MQTT defaults to private Railway DNS
    (mqtt.railway.internal:1883). Local/laptop runs should override via env.
    """
    _load_local_env()

    if ingest_defaults:
        default_host = "mqtt.railway.internal"
        default_port = "1883"
    else:
        default_host = "altaria.proxy.rlwy.net"
        default_port = "33239"

    device_raw = os.environ.get("DEVICE_IDS", "")
    device_ids = tuple(x.strip() for x in device_raw.split(",") if x.strip())

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        # Local default for smoke tests
        database_url = "postgresql://dragino:dragino@127.0.0.1:5432/dragino"

    return Settings(
        database_url=database_url,
        mqtt_host=os.environ.get("MQTT_HOST", default_host),
        mqtt_port=int(os.environ.get("MQTT_PORT", default_port)),
        mqtt_user=os.environ.get("MQTT_USER", "dragino"),
        mqtt_pass=os.environ.get("MQTT_PASS", ""),
        mqtt_topic=os.environ.get("MQTT_TOPIC", "dragino/+/up"),
        # Empty by default: devices appear on first uplink as {model}-{IMEI}.
        # Optional seeds (e.g. DEVICE_IDS=ps-cb,ltc2) still work for placeholders.
        device_ids=device_ids,
        stale_after_hours=int(os.environ.get("STALE_AFTER_HOURS", "24")),
        basic_auth_user=os.environ.get("BASIC_AUTH_USER", "admin"),
        basic_auth_password=os.environ.get("BASIC_AUTH_PASSWORD", ""),
        messages_per_device=int(os.environ.get("MESSAGES_PER_DEVICE", "50")),
        refresh_seconds=int(os.environ.get("REFRESH_SECONDS", "60")),
    )
