"""Bearer auth rules (API.md §2) and OpenAPI scope."""
from __future__ import annotations

import pytest

from dashboard.tests.api_support import *  # noqa: F403 - fixtures
from dashboard.tests.api_support import (
    DEV, RO, RO_H, RW, RW_H, add_device, set_env,
)

SENSOR_BODY = {"unit": "m", "range_low": 0, "range_high": 10}


def test_no_tokens_configured_is_503(client, api_env):
    set_env(api_env, API_TOKEN_RW="", API_TOKEN_RO="")
    r = client.get("/api/v1/devices", headers=RW_H)
    assert r.status_code == 503
    assert r.json() == {"detail": "API tokens not configured"}
    assert client.post(f"/api/v1/devices/{DEV}/sensors", json=SENSOR_BODY).status_code == 503


def test_health_needs_no_auth_even_without_tokens(client, api_env):
    set_env(api_env, API_TOKEN_RW="", API_TOKEN_RO="")
    assert client.get("/api/v1/health").status_code == 200


def test_missing_and_invalid_token_is_401(client):
    for headers in (
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Bearer "},
        {"Authorization": f"Basic {RW}"},
        {"Authorization": RW},
    ):
        r = client.get("/api/v1/devices", headers=headers)
        assert r.status_code == 401, headers
        assert r.headers["www-authenticate"] == "Bearer"
        assert "detail" in r.json()


def test_auth_is_checked_before_lookup_and_validation(client):
    assert client.get("/api/v1/devices/unknown").status_code == 401
    assert client.post("/api/v1/devices/unknown/sensors", json={}).status_code == 401


def test_read_only_token_reads_but_cannot_write(client, api_conn):
    add_device(api_conn)
    assert client.get("/api/v1/devices", headers=RO_H).status_code == 200
    assert client.get(f"/api/v1/devices/{DEV}/sensors", headers=RO_H).status_code == 200
    base = f"/api/v1/devices/{DEV}"
    for r in (
        client.patch(base, json={"label": "x"}, headers=RO_H),
        client.post(f"{base}/sensors", json=SENSOR_BODY, headers=RO_H),
        client.put(f"{base}/sensors/1", json=SENSOR_BODY, headers=RO_H),
        client.delete(f"{base}/sensors/1", headers=RO_H),
    ):
        assert r.status_code == 403
        assert "detail" in r.json()


def test_read_write_token_does_both(client, api_conn):
    add_device(api_conn)
    assert client.get("/api/v1/devices", headers=RW_H).status_code == 200
    r = client.post(f"/api/v1/devices/{DEV}/sensors", json=SENSOR_BODY, headers=RW_H)
    assert r.status_code == 201


def test_only_ro_configured(client, api_env, api_conn):
    set_env(api_env, API_TOKEN_RW="")
    add_device(api_conn)
    assert client.get("/api/v1/devices", headers=RO_H).status_code == 200
    # an unset RW token must not match an empty/any credential
    assert client.get("/api/v1/devices", headers={"Authorization": "Bearer x"}).status_code == 401
    assert client.patch(f"/api/v1/devices/{DEV}", json={"label": "x"}, headers=RO_H).status_code == 403


def test_only_rw_configured(client, api_env, api_conn):
    set_env(api_env, API_TOKEN_RO="")
    add_device(api_conn)
    assert client.get("/api/v1/devices", headers=RW_H).status_code == 200
    assert client.get("/api/v1/devices", headers={"Authorization": f"Bearer {RO}"}).status_code == 401


def test_openapi_covers_only_api_routes(client):
    spec = client.get("/api/openapi.json").json()
    paths = list(spec["paths"])
    assert paths and all(p.startswith("/api/v1/") for p in paths)
    assert "/healthz" not in spec["paths"] and "/" not in spec["paths"]
    assert client.get("/api/docs").status_code == 200
    assert spec["components"]["securitySchemes"]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_default_docs_routes_are_off(client, path):
    assert client.get(path).status_code in (401, 404)
