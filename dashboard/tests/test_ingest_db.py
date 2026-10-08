"""DB-backed ingest tests (local Postgres, see conftest)."""
from __future__ import annotations

import logging
from datetime import timedelta

import psycopg
import pytest

from dashboard.db import connect, record_uplink, store_message
from dashboard.extract import extract_common
from dashboard.ingest import DbGuard, IngestFatal, IngestWorker
from dashboard.tests.conftest import IMEI, NOW, uplink_payload

TOPIC = "dragino/ps-cb/up"
ACK = {"IMEI": IMEI, "Downklink_Ack": "success"}
OTA = {"IMEI": IMEI, "OTA": "applied", "Version": "openfw-0.3.2"}
DEAD_URL = "postgresql://dragino:dragino@127.0.0.1:1/none"  # connection refused


def q(conn: psycopg.Connection, sql: str, *args: object) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = list(cur.fetchall()) if cur.description else []
    conn.commit()
    return rows


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_store_uplink(conn: psycopg.Connection) -> None:
    stored = store_message(conn, topic=TOPIC, received_at=NOW, payload=uplink_payload())
    assert stored is not None
    assert (stored.device_id, stored.kind, stored.readings) == (f"ps-cb-{IMEI}", "uplink", 3)
    (dev,) = q(conn, "SELECT * FROM devices")
    assert (dev["id"], dev["imei"], dev["model"], dev["created_via"]) == (
        f"ps-cb-{IMEI}", IMEI, "ps-cb", "auto")
    (up,) = q(conn, "SELECT * FROM uplinks")
    assert (up["kind"], up["fw_version"], up["battery"], up["signal"]) == ("uplink", None, 3.512, 13)
    rows = q(conn, "SELECT source, idc_ma, uplink_id FROM readings ORDER BY t DESC")
    assert [r["source"] for r in rows] == ["uplink", "clocklog", "clocklog"]
    assert {r["uplink_id"] for r in rows} == {up["id"]}


def test_clocklog_dedupe_across_uplinks(conn: psycopg.Connection) -> None:
    store_message(conn, topic=TOPIC, received_at=NOW, payload=uplink_payload())
    second = uplink_payload(
        time="2026-10-08T12:31:02Z",
        **{"1": [3.97, 0.0, "2026-10-08T10:31:00Z"], "2": [3.961, 0.0, "2026-10-08T08:31:00Z"]},
    )  # "2" repeats the previous "1"
    stored = store_message(conn, topic=TOPIC, received_at=NOW + timedelta(hours=2), payload=second)
    assert stored is not None and stored.readings == 2  # 3 candidates, one duplicate
    assert len(q(conn, "SELECT 1 FROM readings")) == 5
    assert len(q(conn, "SELECT 1 FROM uplinks")) == 2


def test_imei_only_ack_goes_to_model_device(conn: psycopg.Connection) -> None:
    store_message(conn, topic=TOPIC, received_at=NOW, payload=uplink_payload())
    stored = store_message(conn, topic=TOPIC, received_at=NOW + timedelta(minutes=1), payload=ACK)
    assert stored is not None
    assert (stored.device_id, stored.kind, stored.readings) == (f"ps-cb-{IMEI}", "dl_ack", 0)
    assert [r["id"] for r in q(conn, "SELECT id FROM devices")] == [f"ps-cb-{IMEI}"]
    (dev,) = q(conn, "SELECT last_seen_at, imei, model FROM devices")
    assert dev["last_seen_at"] == NOW + timedelta(minutes=1)
    assert (dev["imei"], dev["model"]) == (IMEI, "ps-cb")  # not nulled out by the model-less message


def test_ota_and_status_kinds(conn: psycopg.Connection) -> None:
    store_message(conn, topic=TOPIC, received_at=NOW, payload=uplink_payload())
    store_message(conn, topic=TOPIC, received_at=NOW, payload=OTA)
    status = {"IMEI": IMEI, "Image Version": "openfw-0.3.2", "NB-IoT Stack": "BG95", "Model": "PS-CB"}
    store_message(conn, topic=TOPIC, received_at=NOW, payload=status)
    rows = q(conn, "SELECT kind, fw_version FROM uplinks ORDER BY id")
    assert [(r["kind"], r["fw_version"]) for r in rows] == [
        ("uplink", None), ("ota", "openfw-0.3.2"), ("status", "openfw-0.3.2")]
    assert len(q(conn, "SELECT 1 FROM readings")) == 3  # only the uplink produced readings


def test_ack_before_any_uplink_then_rehomed_live(conn: psycopg.Connection) -> None:
    first = store_message(conn, topic=TOPIC, received_at=NOW, payload=ACK)
    assert first is not None and first.device_id == IMEI
    store_message(conn, topic=TOPIC, received_at=NOW + timedelta(minutes=5), payload=uplink_payload())
    assert [r["id"] for r in q(conn, "SELECT id FROM devices")] == [f"ps-cb-{IMEI}"]
    assert {r["device_id"] for r in q(conn, "SELECT device_id FROM uplinks")} == {f"ps-cb-{IMEI}"}
    (dev,) = q(conn, "SELECT first_seen_at FROM devices")
    assert dev["first_seen_at"] == NOW  # earliest sighting survives the merge


def test_ack_maps_via_imei_column(conn: psycopg.Connection) -> None:
    q(conn, "INSERT INTO devices (id, created_via, imei) VALUES ('bench-unit', 'seed', %s)", IMEI)
    stored = store_message(conn, topic=TOPIC, received_at=NOW, payload=OTA)
    assert stored is not None and stored.device_id == "bench-unit"


def test_unknown_topic_without_imei_is_ignored(conn: psycopg.Connection) -> None:
    assert store_message(conn, topic="weird", received_at=NOW, payload={"_raw": "x"}) is None
    assert q(conn, "SELECT 1 FROM uplinks") == []


def test_record_uplink_compat(conn: psycopg.Connection) -> None:
    payload = uplink_payload()
    n = record_uplink(
        conn, device_id=f"ps-cb-{IMEI}", topic=TOPIC, received_at=NOW, payload=payload,
        extracts=extract_common(payload), known_seed=False)
    assert n == 3


# --- reliability ---------------------------------------------------------

def kill_backend(db_url: str, victim: psycopg.Connection) -> None:
    pid = victim.info.backend_pid
    with psycopg.connect(db_url, autocommit=True) as other:
        other.execute("SELECT pg_terminate_backend(%s)", (pid,))


def test_guard_survives_killed_connection(db_url: str, conn: psycopg.Connection) -> None:
    guard = DbGuard(db_url)
    guard.open()
    try:
        assert guard.persist(TOPIC, NOW, uplink_payload()) is not None
        assert guard.reconnects == 0
        old_pid = guard.conn.info.backend_pid
        kill_backend(db_url, guard.conn)

        stored = guard.persist(
            TOPIC, NOW + timedelta(hours=2),
            uplink_payload(time="2026-10-08T12:31:02Z", **{"1": [3.9, 0.0, "2026-10-08T10:31:00Z"]}))
        assert stored is not None and stored.readings >= 2
        assert guard.reconnects == 1 and guard.failures == 0
        assert guard.conn.info.backend_pid != old_pid
        assert len(q(conn, "SELECT 1 FROM uplinks")) == 2
    finally:
        guard.close()


def test_guard_survives_closed_connection_object(db_url: str, conn: psycopg.Connection) -> None:
    guard = DbGuard(db_url)
    guard.open()
    try:
        guard.conn.close()  # "the connection is closed" -- the production failure
        assert guard.persist(TOPIC, NOW, uplink_payload()) is not None
        assert guard.reconnects == 1
    finally:
        guard.close()


def test_idle_probe_replaces_stale_connection(db_url: str, conn: psycopg.Connection) -> None:
    clock = FakeClock()
    guard = DbGuard(db_url, clock=clock, probe_after=300)
    guard.open()
    try:
        guard.persist(TOPIC, NOW, uplink_payload())
        kill_backend(db_url, guard.conn)
        clock.t += 301  # idle: next message runs SELECT 1 first, it fails, we reconnect
        assert guard.persist(TOPIC, NOW + timedelta(hours=2), ACK) is not None
        assert guard.reconnects == 1
        assert len(q(conn, "SELECT 1 FROM uplinks")) == 2
    finally:
        guard.close()


def test_fatal_after_consecutive_failures() -> None:
    guard = DbGuard(DEAD_URL, max_failures=3, stall_seconds=10**9)
    for _ in range(2):
        assert guard.persist(TOPIC, NOW, ACK) is None
    assert guard.failures == 2
    with pytest.raises(IngestFatal, match="3 consecutive"):
        guard.persist(TOPIC, NOW, ACK)


def test_fatal_after_stall() -> None:
    clock = FakeClock()
    guard = DbGuard(DEAD_URL, max_failures=100, stall_seconds=1800, clock=clock)
    assert guard.persist(TOPIC, NOW, ACK) is None  # first failure starts the stall clock
    clock.t += 1799
    assert guard.persist(TOPIC, NOW, ACK) is None
    clock.t += 2
    with pytest.raises(IngestFatal, match="no successful persist"):
        guard.persist(TOPIC, NOW, ACK)


def test_success_resets_failure_run(db_url: str, conn: psycopg.Connection) -> None:
    guard = DbGuard(DEAD_URL, max_failures=3, stall_seconds=10**9)
    guard.persist(TOPIC, NOW, ACK)
    guard.persist(TOPIC, NOW, ACK)
    assert guard.failures == 2
    guard.database_url = db_url  # database comes back
    assert guard.persist(TOPIC, NOW, uplink_payload()) is not None
    assert (guard.failures, guard.first_failure_at) == (0, None)


def test_poison_payload_is_dropped_not_counted(db_url: str, conn: psycopg.Connection) -> None:
    guard = DbGuard(db_url, max_failures=1)
    guard.open()
    try:
        assert guard.persist(TOPIC, NOW, uplink_payload(Model="PS\u0000CB")) is None
        assert guard.failures == 0
        assert guard.persist(TOPIC, NOW, uplink_payload()) is not None  # connection still usable
    finally:
        guard.close()


class FakeClient:
    def __init__(self) -> None:
        self.disconnects = 0

    def disconnect(self) -> None:
        self.disconnects += 1


def test_worker_exit_code_on_fatal() -> None:
    guard = DbGuard(DEAD_URL, max_failures=2)
    client = FakeClient()
    worker = IngestWorker(guard, client, hard_exit_after=None)
    raw = b'{"IMEI":"869181074164029","Downklink_Ack":"success"}'
    worker.handle(TOPIC, raw)
    assert (worker.exit_code, worker.stopping, client.disconnects) == (0, False, 0)
    worker.handle(TOPIC, raw)
    assert (worker.exit_code, worker.stopping, client.disconnects) == (1, True, 1)


def test_worker_logs_stored_uplink(db_url: str, conn: psycopg.Connection, caplog: pytest.LogCaptureFixture) -> None:
    guard = DbGuard(db_url)
    guard.open()
    worker = IngestWorker(guard, FakeClient(), hard_exit_after=None)
    try:
        with caplog.at_level(logging.INFO, logger="dashboard.ingest"):
            worker.handle(TOPIC, b'{"IMEI":"869181074164029","Model":"PS-CB","idc_input":3.962,"battery":3.5}')
    finally:
        guard.close()
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("uplink ")]
    assert "device=ps-cb-869181074164029 kind=uplink readings=1" in line


def test_connect_uses_timeouts(db_url: str) -> None:
    with connect(db_url) as c:
        assert c.info.get_parameters().get("connect_timeout") == "10"
