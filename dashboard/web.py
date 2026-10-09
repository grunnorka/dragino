"""Fleet dashboard (HTML, HTTP Basic Auth) + JSON API v1 (bearer token).

Run: uvicorn dashboard.web:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from dashboard import api, queries
from dashboard.api import Conn, SensorIn
from dashboard.charts import build_chart, fmt_num
from dashboard.db import connect, ensure_schema
from dashboard.settings import Settings, get_settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
security = HTTPBasic()

DESCRIPTION = """\
Telemetry API for the Dragino sensor fleet. All routes except `/health` need
`Authorization: Bearer <token>` (`API_TOKEN_RW` read+write, `API_TOKEN_RO` read
only). Contract: `dashboard/API.md`.
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    with connect(settings.database_url) as conn:
        ensure_schema(conn, settings.device_ids)
    yield


app = FastAPI(
    title="Dragino telemetry API",
    version="1",
    description=DESCRIPTION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(api.router)


@app.exception_handler(psycopg.OperationalError)
def db_unavailable(request: Request, _exc: psycopg.OperationalError) -> Any:
    # The DB went away mid-request: say so (and let monitors see a 503).
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "database unavailable"}, status_code=503)
    return PlainTextResponse("database unavailable", status_code=503)


# --- HTML auth + CSRF ---------------------------------------------------------------


def require_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
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


Auth = Annotated[str, Depends(require_auth)]


def csrf_guard(request: Request) -> None:
    """Browsers resend Basic credentials automatically, so POSTs must come from
    our own pages: Origin (or Referer) host has to match the request host."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin or origin == "null":
        raise HTTPException(status_code=403, detail="Missing Origin/Referer")
    host = (urlsplit(origin).netloc or "").lower()
    allowed = {request.headers.get("host", "").lower()}
    forwarded = request.headers.get("x-forwarded-host")
    if forwarded:
        allowed.add(forwarded.split(",")[0].strip().lower())
    allowed.discard("")
    if host not in allowed:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")


Csrf = Annotated[None, Depends(csrf_guard)]
MAX_FORM_BYTES = 16 * 1024


async def form_body(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise HTTPException(status_code=413, detail="Form too large")
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[-1] for k, v in parsed.items()}


Form = Annotated[dict[str, str], Depends(form_body)]

# --- template helpers -------------------------------------------------------------------


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return queries.to_utc(value).strftime("%Y-%m-%d %H:%M:%SZ")


def _ago(value: datetime | None) -> str:
    if value is None:
        return ""
    secs = int((datetime.now(timezone.utc) - queries.to_utc(value)).total_seconds())
    if secs < 0:
        return "in the future"
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{round(secs / 60)} min ago"
    if secs < 2 * 86400:
        return f"{secs / 3600:.1f} h ago"
    return f"{secs / 86400:.1f} d ago"


def _fmt_minutes(value: datetime | None) -> str:
    return "—" if value is None else queries.to_utc(value).strftime("%Y-%m-%d %H:%M")


TEMPLATES.env.filters["fmt_dt"] = _fmt_dt
TEMPLATES.env.filters["fmt_min"] = _fmt_minutes
TEMPLATES.env.filters["ago"] = _ago
TEMPLATES.env.filters["num"] = fmt_num
TEMPLATES.env.filters["quality_label"] = lambda q: (q or "").replace("_", " ")

NOTICES = {
    "label-saved": "Label saved.",
    "sensor-added": "Calibration period added.",
}

RANGES: dict[str, tuple[timedelta, str]] = {
    "24h": (timedelta(hours=24), "raw"),
    "7d": (timedelta(days=7), "raw"),
    "30d": (timedelta(days=30), "1h"),
}
DEFAULT_RANGE = "7d"
RAW_CHART_CAP = 2000  # more raw points than this in the window -> hourly buckets


def chart_for(
    conn: psycopg.Connection, device_id: str, range_key: str
) -> dict[str, Any] | None:
    span, bucket = RANGES[range_key]
    t1 = datetime.now(timezone.utc)
    t0 = t1 - span
    downsampled = False
    if bucket == "raw":
        rows, more = queries.readings_raw(conn, device_id, t0, t1, RAW_CHART_CAP)
        if more:
            bucket, downsampled = "1h", True
    if bucket != "raw":
        rows = queries.readings_bucketed(conn, device_id, bucket, t0, t1, 20000)
    if not rows:
        return None

    # Calibrated value if the newest sample has one, else raw mA.
    unit, field = "mA", "idc_ma"
    for row in reversed(rows):
        if row["value"] is not None:
            unit, field = row["unit"] or "", "value"
            break
    points = [
        {"t": r["t"], "v": r[field], "q": r["quality"]}
        for r in rows
        if r[field] is not None and (field != "value" or r["unit"] == unit)
    ]
    chart = build_chart(
        points, t0, t1, unit, bucket_seconds=3600 if bucket == "1h" else 0
    )
    if chart is not None:
        chart["bucket"] = bucket
        chart["downsampled"] = downsampled
        chart["calibrated"] = field == "value"
    return chart


def _redirect(device_id: str, **params: str) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(
        f"/devices/{quote(device_id, safe='')}" + (f"?{query}" if query else "") + "#calibration",
        status_code=303,
    )


# --- HTML routes ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def fleet(
    request: Request,
    _user: Auth,
    conn: Conn,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    devices = queries.list_devices(conn, settings.stale_after_hours)
    newest = queries.last_uplink_at(conn)
    now = datetime.now(timezone.utc)
    stalled = newest is None or now - queries.to_utc(newest) > timedelta(
        hours=settings.stale_after_hours
    )
    return TEMPLATES.TemplateResponse(
        request,
        "fleet.html",
        {
            "devices": devices,
            "newest_uplink": newest,
            "ingest_stalled": stalled,
            "stale_after_hours": settings.stale_after_hours,
            "refresh_seconds": settings.refresh_seconds,
            "now": now,
        },
    )


@app.get("/devices/{device_id}", response_class=HTMLResponse, include_in_schema=False)
def device_detail(
    request: Request,
    device_id: str,
    _user: Auth,
    conn: Conn,
    settings: Annotated[Settings, Depends(get_settings)],
    range: str = DEFAULT_RANGE,  # noqa: A002 - query parameter name
    notice: str = "",
    error: str = "",
) -> HTMLResponse:
    device = queries.get_device(conn, device_id, settings.stale_after_hours)
    if device is None:
        raise HTTPException(status_code=404, detail="Unknown device")
    range_key = range if range in RANGES else DEFAULT_RANGE
    return TEMPLATES.TemplateResponse(
        request,
        "device.html",
        {
            "device": device,
            "chart": chart_for(conn, device_id, range_key),
            "range_key": range_key,
            "ranges": list(RANGES),
            "events": queries.list_events(conn, device_id, limit=25),
            "sensors": queries.list_sensors(conn, device_id),
            "uplinks": queries.list_uplinks(conn, device_id, limit=settings.messages_per_device),
            "notice": NOTICES.get(notice),
            "error": error[:300],
            "stale_after_hours": settings.stale_after_hours,
            "limit": settings.messages_per_device,
            "now": datetime.now(timezone.utc),
        },
    )


@app.post("/devices/{device_id}/label", include_in_schema=False)
def post_label(
    device_id: str, _user: Auth, _csrf: Csrf, form: Form, conn: Conn
) -> RedirectResponse:
    label = (form.get("label") or "").strip()[:120] or None
    if not queries.set_label(conn, device_id, label):
        raise HTTPException(status_code=404, detail="Unknown device")
    return _redirect(device_id, notice="label-saved")


_FLOAT_FIELDS = ("in_low", "in_high", "range_low", "range_high", "offset")


@app.post("/devices/{device_id}/sensors", include_in_schema=False)
def post_sensor(
    device_id: str, _user: Auth, _csrf: Csrf, form: Form, conn: Conn
) -> RedirectResponse:
    if not queries.device_exists(conn, device_id):
        raise HTTPException(status_code=404, detail="Unknown device")
    data: dict[str, str] = {}
    for key in ("valid_from", "channel", "kind", "unit", "label", *_FLOAT_FIELDS):
        value = (form.get(key) or "").strip()
        if value:  # blank -> model default (or "required" error)
            data[key] = value
    try:
        sensor = SensorIn.model_validate(data)
    except ValidationError as exc:
        msg = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'form'}: {e['msg']}" for e in exc.errors()
        )
        return _redirect(device_id, error=msg[:300])
    try:
        queries.insert_sensor(conn, device_id, sensor.row())
    except queries.DuplicateValidFrom:
        return _redirect(device_id, error="A period with that valid_from already exists")
    return _redirect(device_id, notice="sensor-added")


# --- health -----------------------------------------------------------------------------------


@app.get("/healthz", include_in_schema=False)
def healthz(settings: Annotated[Settings, Depends(get_settings)]) -> Any:
    """Railway healthcheck: runs a real query so a lost DB turns this red."""
    try:
        api.probe_db(settings)
    except Exception:  # noqa: BLE001 - any failure means unhealthy
        return JSONResponse({"status": "error", "db": "error"}, status_code=503)
    return {"status": "ok"}
