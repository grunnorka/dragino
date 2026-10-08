"""Health endpoints really query the DB and fail when it is gone."""
from __future__ import annotations

from datetime import timedelta

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import (
    DEAD_DB_URL, DEV, RW_H, add_device, add_uplink, set_env, utcnow,
)


def test_health_ok_with_last_uplink(client, api_conn):
    t = utcnow() - timedelta(hours=3)
    add_device(api_conn, DEV, last_seen=t)
    add_uplink(api_conn, DEV, received_at=t)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["status", "db", "last_uplink_at"]
    assert body["status"] == "ok" and body["db"] == "ok"
    assert body["last_uplink_at"].endswith("Z")
    assert body["last_uplink_at"][:16] == t.strftime("%Y-%m-%dT%H:%M")


def test_health_empty_db_has_null_last_uplink(client, api_conn):
    assert client.get("/api/v1/health").json() == {"status": "ok", "db": "ok", "last_uplink_at": None}


def test_health_503_when_db_unreachable(client, api_env):
    set_env(api_env, DATABASE_URL=DEAD_DB_URL)
    r = client.get("/api/v1/health")
    assert r.status_code == 503
    assert r.json()["status"] == "error" and r.json()["db"] == "error"


def test_healthz_ok_and_503(client, api_env, api_conn):
    assert client.get("/healthz").json() == {"status": "ok"}
    set_env(api_env, DATABASE_URL=DEAD_DB_URL)
    assert client.get("/healthz").status_code == 503


def test_healthz_503_when_schema_missing(client, api_conn):
    with api_conn.cursor() as cur:
        cur.execute("ALTER TABLE uplinks RENAME TO uplinks_gone")
    api_conn.commit()
    try:
        assert client.get("/healthz").status_code == 503
    finally:
        with api_conn.cursor() as cur:
            cur.execute("ALTER TABLE uplinks_gone RENAME TO uplinks")
        api_conn.commit()


def test_authenticated_route_503_when_db_down(client, api_env):
    set_env(api_env, DATABASE_URL=DEAD_DB_URL)
    r = client.get("/api/v1/devices", headers=RW_H)
    assert r.status_code == 503 and r.json() == {"detail": "database unavailable"}
    # auth still wins over the DB error
    assert client.get("/api/v1/devices").status_code == 401


def test_no_connection_is_kept_between_requests(client, api_conn):
    add_device(api_conn, DEV, last_seen=utcnow())
    with api_conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()")
        before = cur.fetchone()["n"]
    for _ in range(5):
        assert client.get("/api/v1/devices", headers=RW_H).status_code == 200
        assert client.get("/healthz").status_code == 200
    with api_conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()")
        assert cur.fetchone()["n"] == before
