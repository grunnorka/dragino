"""Shared fixtures/helpers for the API + web tests (database dragino_api, ours alone).

Test modules do ``from dashboard.tests.api_support import *`` to get the
fixtures. Settings come from explicit env vars set before the settings cache
is rebuilt; nothing reads a real env file because every key is set here.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from dashboard import settings as settings_mod
from dashboard.db import connect, ensure_schema

API_DB_URL = os.environ.get(
    "API_TEST_DATABASE_URL", "postgresql://dragino:dragino@127.0.0.1:55432/dragino_api"
)
DEAD_DB_URL = "postgresql://dragino:dragino@127.0.0.1:1/dragino_api"  # closed port
RW = "rw-token-for-tests"
RO = "ro-token-for-tests"
BASIC = ("admin", "basic-pass-for-tests")
DEV = "ps-cb-869181074164029"
DEV2 = "ltc2-869181074164999"
RW_H = {"Authorization": f"Bearer {RW}"}
RO_H = {"Authorization": f"Bearer {RO}"}

__all__ = [
    "API_DB_URL", "DEAD_DB_URL", "RW", "RO", "BASIC", "DEV", "DEV2", "RW_H", "RO_H",
    "api_conn", "api_env", "client", "set_env", "reload_settings",
    "add_device", "add_uplink", "add_reading", "add_sensor", "hour_floor", "utcnow",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hour_floor(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def reload_settings() -> None:
    settings_mod.reset_settings()


@pytest.fixture()
def api_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Explicit env for every setting the web app reads, then a fresh cache."""
    for key, val in {
        "DATABASE_URL": API_DB_URL,
        "API_TOKEN_RW": RW,
        "API_TOKEN_RO": RO,
        "BASIC_AUTH_USER": BASIC[0],
        "BASIC_AUTH_PASSWORD": BASIC[1],
        "STALE_AFTER_HOURS": "24",
        "DEVICE_IDS": "",
        "MESSAGES_PER_DEVICE": "50",
        "REFRESH_SECONDS": "60",
    }.items():
        monkeypatch.setenv(key, val)
    reload_settings()
    yield monkeypatch
    reload_settings()


def set_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    reload_settings()


@pytest.fixture()
def api_conn() -> Iterator[psycopg.Connection]:
    name = API_DB_URL.rsplit("/", 1)[-1].split("?")[0]
    if "api" not in name:
        pytest.exit(f"refusing to reset database {name!r}: name must contain 'api'")
    try:
        c = connect(API_DB_URL)
    except psycopg.Error as exc:
        msg = f"test Postgres unreachable ({type(exc).__name__}); start podman container telemetry-pg"
        if os.environ.get("REQUIRE_DB"):
            pytest.fail(msg)
        pytest.skip(msg)
    ensure_schema(c, ())
    with c.cursor() as cur:
        cur.execute("TRUNCATE readings, sensors, uplinks, devices RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def client(api_env: pytest.MonkeyPatch, api_conn: psycopg.Connection) -> TestClient:
    from dashboard.web import app

    return TestClient(app)


# --- seed helpers (plain SQL, independent of the ingest writer) ------------------


def add_device(
    c: psycopg.Connection,
    device_id: str = DEV,
    *,
    label: str | None = None,
    model: str | None = None,
    imei: str | None = None,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
) -> str:
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO devices (id, label, model, imei, first_seen_at, last_seen_at, created_via)
            VALUES (%s, %s, %s, %s, %s, %s, 'auto')
            """,
            (device_id, label, model, imei, first_seen, last_seen),
        )
    c.commit()
    return device_id


def add_uplink(
    c: psycopg.Connection,
    device_id: str = DEV,
    *,
    kind: str = "uplink",
    payload: dict[str, Any] | None = None,
    received_at: datetime | None = None,
    battery: float | None = None,
    signal: float | None = None,
    fw_version: str | None = None,
    topic: str = "dragino/ps-cb/up",
) -> int:
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO uplinks (device_id, topic, received_at, payload, battery, "signal",
                                 kind, fw_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                device_id, topic, received_at or utcnow(), Jsonb(payload or {}),
                battery, signal, kind, fw_version,
            ),
        )
        row = cur.fetchone()
    c.commit()
    return row["id"]


def add_reading(
    c: psycopg.Connection,
    device_id: str,
    t: datetime,
    idc_ma: float | None,
    *,
    vdc_v: float | None = 0.0,
    source: str = "uplink",
    temp1_c: float | None = None,
) -> None:
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO readings (device_id, t, source, idc_ma, vdc_v, temp1_c)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (device_id, t, source, idc_ma, vdc_v, temp1_c),
        )
    c.commit()


def add_sensor(
    c: psycopg.Connection,
    device_id: str,
    valid_from: datetime,
    *,
    unit: str = "m",
    range_low: float = 0,
    range_high: float = 10,
    channel: str = "idc",
    kind: str = "level",
    offset: float = 0,
    label: str | None = None,
) -> int:
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sensors (device_id, valid_from, channel, kind, unit,
                                 range_low, range_high, "offset", label)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (device_id, valid_from, channel, kind, unit, range_low, range_high, offset, label),
        )
        row = cur.fetchone()
    c.commit()
    return row["id"]


def dumps(obj: Any) -> str:
    return json.dumps(obj)
