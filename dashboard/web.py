"""Read-only FastAPI fleet dashboard (HTTP Basic Auth).

Run: uvicorn dashboard.web:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from dashboard.db import connect, ensure_schema, get_device, list_fleet, list_uplinks
from dashboard.settings import Settings, load_settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
security = HTTPBasic()

app = FastAPI(title="Dragino fleet", docs_url=None, redoc_url=None)
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings(ingest_defaults=False)
    return _settings


@app.on_event("startup")
def startup() -> None:
    settings = get_settings()
    with connect(settings.database_url) as conn:
        ensure_schema(conn, settings.device_ids)


def require_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
) -> str:
    settings = get_settings()
    if not settings.basic_auth_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BASIC_AUTH_PASSWORD is not configured",
        )
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.basic_auth_user.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.basic_auth_password.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


TEMPLATES.env.filters["fmt_dt"] = _fmt_dt


@app.get("/", response_class=HTMLResponse)
def fleet(
    request: Request,
    _user: Annotated[str, Depends(require_auth)],
) -> HTMLResponse:
    settings = get_settings()
    with connect(settings.database_url) as conn:
        devices = list_fleet(conn, settings.stale_after_hours)
    return TEMPLATES.TemplateResponse(
        request,
        "fleet.html",
        {
            "devices": devices,
            "stale_after_hours": settings.stale_after_hours,
            "refresh_seconds": settings.refresh_seconds,
            "now": datetime.now(timezone.utc),
        },
    )


@app.get("/devices/{device_id}", response_class=HTMLResponse)
def device_detail(
    request: Request,
    device_id: str,
    _user: Annotated[str, Depends(require_auth)],
) -> HTMLResponse:
    settings = get_settings()
    with connect(settings.database_url) as conn:
        device = get_device(conn, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Unknown device")
        uplinks = list_uplinks(conn, device_id, limit=settings.messages_per_device)
        fleet_rows = {d["id"]: d for d in list_fleet(conn, settings.stale_after_hours)}
    status_row = fleet_rows.get(device_id, {})
    return TEMPLATES.TemplateResponse(
        request,
        "device.html",
        {
            "device": device,
            "uplinks": uplinks,
            "status": status_row.get("status", "never-seen"),
            "stale": status_row.get("stale", True),
            "battery": status_row.get("battery"),
            "signal": status_row.get("signal"),
            "model": status_row.get("model"),
            "stale_after_hours": settings.stale_after_hours,
            "refresh_seconds": settings.refresh_seconds,
            "limit": settings.messages_per_device,
            "now": datetime.now(timezone.utc),
        },
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
