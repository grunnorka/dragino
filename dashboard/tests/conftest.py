"""Shared fixtures. DB tests use a throw-away local Postgres.

Default: ``postgresql://dragino:dragino@127.0.0.1:55432/dragino_ingest`` (podman
container ``telemetry-pg``); override with ``TEST_DATABASE_URL``. The tables are
dropped and recreated per test, so the database name must contain "ingest" or
"test". Set ``REQUIRE_DB=1`` to fail instead of skip when it is unreachable.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone

import psycopg
import pytest

from dashboard.db import connect, ensure_schema

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://dragino:dragino@127.0.0.1:55432/dragino_ingest"
)
IMEI = "869181074164029"

NOW = datetime(2026, 10, 8, 10, 31, 5, tzinfo=timezone.utc)


def uplink_payload(**over: object) -> dict:
    """PS-CB openfw uplink as in PAYLOADS.md §1 / the handoff."""
    payload: dict = {
        "IMEI": IMEI,
        "IMSI": "274012011385126",
        "Model": "PS-CB",
        "idc_input": 3.962,
        "vdc_input": 0.0,
        "interrupt": 0,
        "interrupt_level": 0,
        "battery": 3.512,
        "signal": 13,
        "idc_alarm": "NULL",
        "vdc_alarm": "NULL",
        "time": "2026-10-08T10:31:02Z",
        "latitude": 0.0,
        "longitude": 0.0,
        "gps_time": "1970-01-01T00:00:00Z",
        "1": [3.961, 0.0, "2026-10-08T08:31:00Z"],
        "2": [3.962, 0.0, "2026-10-08T06:31:00Z"],
    }
    payload.update(over)
    return payload


@pytest.fixture(scope="session")
def db_url() -> str:
    name = TEST_DB_URL.rsplit("/", 1)[-1].split("?")[0]
    if "ingest" not in name and "test" not in name:
        pytest.exit(f"refusing to reset database {name!r}: name must contain 'ingest' or 'test'")
    try:
        psycopg.connect(TEST_DB_URL, connect_timeout=3).close()
    except psycopg.Error as exc:
        msg = f"test Postgres unreachable ({type(exc).__name__}); start podman container telemetry-pg"
        if os.environ.get("REQUIRE_DB"):
            pytest.fail(msg)
        pytest.skip(msg)
    return TEST_DB_URL


@pytest.fixture()
def conn(db_url: str) -> Iterator[psycopg.Connection]:
    c = connect(db_url)
    with c.cursor() as cur:
        cur.execute("DROP VIEW IF EXISTS readings_scaled")
        cur.execute("DROP TABLE IF EXISTS readings, sensors, uplinks, devices CASCADE")
    c.commit()
    ensure_schema(c, ())
    yield c
    c.close()
