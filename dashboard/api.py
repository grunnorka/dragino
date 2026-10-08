"""JSON API v1 (bearer token); contract in dashboard/API.md.

Mounted by web.py. A database connection is opened per request and closed
afterwards -- never a global long-lived one.
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dashboard import queries
from dashboard.queries import iso, to_utc
from dashboard.settings import Settings, get_settings

# --- DB dependency -------------------------------------------------------------


def db_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[psycopg.Connection]:
    conn = queries.open_conn(settings.database_url)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[psycopg.Connection, Depends(db_conn)]
Cfg = Annotated[Settings, Depends(get_settings)]

# --- auth -----------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False, description="API_TOKEN_RW (read+write) or API_TOKEN_RO (read)")


def _authenticate(
    creds: HTTPAuthorizationCredentials | None, settings: Settings
) -> Literal["rw", "ro"]:
    rw, ro = settings.api_token_rw, settings.api_token_ro
    if not rw and not ro:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API tokens not configured",
        )
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = creds.credentials.encode("utf-8")
    # Evaluate both comparisons so timing does not reveal which token matched.
    is_rw = secrets.compare_digest(token, rw.encode("utf-8")) and bool(rw)
    is_ro = secrets.compare_digest(token, ro.encode("utf-8")) and bool(ro)
    if is_rw:
        return "rw"
    if is_ro:
        return "ro"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_read(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Cfg,
) -> str:
    return _authenticate(creds, settings)


def require_write(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Cfg,
) -> str:
    scope = _authenticate(creds, settings)
    if scope != "rw":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Read-only token cannot write"
        )
    return scope


READ = [Depends(require_read)]
WRITE = [Depends(require_write)]

# --- models ----------------------------------------------------------------------------

Ts = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


class Reading(BaseModel):
    t: Ts
    source: Literal["uplink", "clocklog"]
    idc_ma: float | None
    vdc_v: float | None
    temp1_c: float | None
    temp2_c: float | None
    value: float | None
    unit: str | None
    quality: Literal["ok", "saturated", "fault", "no_signal"] | None


class BucketReading(BaseModel):
    t: Ts
    n: int
    idc_ma: float | None
    vdc_v: float | None
    value: float | None
    value_min: float | None
    value_max: float | None
    unit: str | None
    quality: Literal["ok", "saturated", "fault", "no_signal"] | None


class SensorOut(BaseModel):
    id: int
    valid_from: Ts
    channel: Literal["idc", "vdc"]
    kind: str
    unit: str
    in_low: float
    in_high: float
    range_low: float
    range_high: float
    offset: float
    label: str | None


class LastOta(BaseModel):
    t: Ts
    result: str | None
    version: str | None
    info: str | None


class DeviceOut(BaseModel):
    id: str
    label: str | None
    model: str | None
    imei: str | None
    first_seen_at: Ts | None
    last_seen_at: Ts | None
    status: Literal["ok", "stale", "never-seen"]
    battery_v: float | None
    signal_csq: int | float | None
    fw_version: str | None
    last_ota: LastOta | None
    sensor: SensorOut | None
    latest: Reading | None


class DevicesOut(BaseModel):
    devices: list[DeviceOut]


class RawReadingsOut(BaseModel):
    device_id: str
    bucket: Literal["raw"]
    readings: list[Reading]
    next_cursor: Ts | None


class BucketReadingsOut(BaseModel):
    device_id: str
    bucket: Literal["1h", "1d"]
    readings: list[BucketReading]
    next_cursor: None = None


class EventOut(BaseModel):
    id: int
    t: Ts
    kind: Literal["ota", "dl_ack", "status"]
    result: str | None
    version: str | None
    info: str | None
    payload: dict[str, Any]


class EventsOut(BaseModel):
    events: list[EventOut]


class SensorsOut(BaseModel):
    sensors: list[SensorOut]


class HealthOut(BaseModel):
    status: str
    db: str
    last_uplink_at: Ts | None


class Detail(BaseModel):
    detail: str


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str | None = Field(description="null or empty clears the label", max_length=120)

    @field_validator("label")
    @classmethod
    def _blank_is_null(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SensorIn(BaseModel):
    """Sensor fields without id; also used for the HTML form."""

    model_config = ConfigDict(extra="ignore")

    valid_from: datetime | None = Field(None, description="defaults to now (UTC)")
    channel: Literal["idc", "vdc"] = "idc"
    kind: str = Field("level", min_length=1, max_length=40)
    unit: str = Field(min_length=1, max_length=20)
    in_low: float = Field(4, allow_inf_nan=False)
    in_high: float = Field(20, allow_inf_nan=False)
    range_low: float = Field(allow_inf_nan=False)
    range_high: float = Field(allow_inf_nan=False)
    offset: float = Field(0, allow_inf_nan=False)
    label: str | None = Field(None, max_length=120)

    @field_validator("unit", "kind")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _check_input_span(self) -> SensorIn:
        if self.in_high == self.in_low:
            raise ValueError("in_high must differ from in_low")
        return self

    def row(self, *, default_valid_from: datetime | None = None) -> dict[str, Any]:
        data = self.model_dump()
        valid_from = self.valid_from or default_valid_from or datetime.now(timezone.utc)
        data["valid_from"] = to_utc(valid_from)
        return data


# --- serializers ---------------------------------------------------------------------------


def _reading_out(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "t": iso(row["t"])}


def _sensor_out(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "valid_from": iso(row["valid_from"])}


def _device_out(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in DeviceOut.model_fields}
    out["first_seen_at"] = iso(row["first_seen_at"])
    out["last_seen_at"] = iso(row["last_seen_at"])
    if row["last_ota"] is not None:
        out["last_ota"] = {**row["last_ota"], "t": iso(row["last_ota"]["t"])}
    if row["sensor"] is not None:
        out["sensor"] = _sensor_out(row["sensor"])
    if row["latest"] is not None:
        out["latest"] = _reading_out(row["latest"])
    return out


def _event_out(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "t": iso(row["t"])}


# --- routes ----------------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["telemetry"])

NOT_FOUND = {404: {"model": Detail, "description": "Unknown device"}}
AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": Detail, "description": "Missing or invalid bearer token"},
    503: {"model": Detail, "description": "API tokens not configured / database unavailable"},
}
WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    403: {"model": Detail, "description": "Read-only token"},
}


def _require_device(conn: psycopg.Connection, device_id: str) -> None:
    if not queries.device_exists(conn, device_id):
        raise HTTPException(status_code=404, detail="Unknown device")


def probe_db(settings: Settings) -> datetime | None:
    """Run a real query on a fresh connection; raises if the DB is unusable."""
    conn = queries.open_conn(settings.database_url)
    try:
        return queries.last_uplink_at(conn)
    finally:
        conn.close()


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
def health(settings: Cfg) -> Any:
    """Liveness plus a real DB query (no auth)."""
    try:
        last = probe_db(settings)
    except Exception:  # noqa: BLE001 - any failure means unhealthy
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "db": "error",
                "last_uplink_at": None,
                "detail": "database unavailable",
            },
        )
    return {"status": "ok", "db": "ok", "last_uplink_at": iso(last)}


@router.get(
    "/devices",
    response_model=DevicesOut,
    dependencies=READ,
    responses={**AUTH_RESPONSES},
)
def list_devices(conn: Conn, settings: Cfg) -> Any:
    rows = queries.list_devices(conn, settings.stale_after_hours)
    return {"devices": [_device_out(r) for r in rows]}


@router.get(
    "/devices/{device_id}",
    response_model=DeviceOut,
    dependencies=READ,
    responses={**AUTH_RESPONSES, **NOT_FOUND},
)
def get_device(device_id: str, conn: Conn, settings: Cfg) -> Any:
    row = queries.get_device(conn, device_id, settings.stale_after_hours)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown device")
    return _device_out(row)


@router.patch(
    "/devices/{device_id}",
    response_model=DeviceOut,
    dependencies=WRITE,
    responses={**WRITE_RESPONSES, **NOT_FOUND},
)
def patch_device(device_id: str, body: DevicePatch, conn: Conn, settings: Cfg) -> Any:
    if not queries.set_label(conn, device_id, body.label):
        raise HTTPException(status_code=404, detail="Unknown device")
    row = queries.get_device(conn, device_id, settings.stale_after_hours)
    assert row is not None
    return _device_out(row)


@router.get(
    "/devices/{device_id}/readings",
    response_model=RawReadingsOut | BucketReadingsOut,
    dependencies=READ,
    responses={**AUTH_RESPONSES, **NOT_FOUND},
)
def get_readings(
    device_id: str,
    conn: Conn,
    from_: Annotated[
        datetime | None, Query(alias="from", description="default: `to` - 7 days")
    ] = None,
    to: Annotated[datetime | None, Query(description="default: now")] = None,
    bucket: Literal["raw", "1h", "1d"] = "raw",
    limit: Annotated[int, Query(ge=1, le=20000)] = 5000,
    cursor: Annotated[
        datetime | None, Query(description="exclusive lower bound; previous `next_cursor`")
    ] = None,
) -> Any:
    t_to = to_utc(to) if to else datetime.now(timezone.utc)
    t_from = to_utc(from_) if from_ else t_to - timedelta(days=7)
    if t_from > t_to:
        raise HTTPException(status_code=422, detail="`from` must not be after `to`")
    _require_device(conn, device_id)
    if bucket == "raw":
        rows, more = queries.readings_raw(
            conn, device_id, t_from, t_to, limit, to_utc(cursor) if cursor else None
        )
        return {
            "device_id": device_id,
            "bucket": "raw",
            "readings": [_reading_out(r) for r in rows],
            "next_cursor": iso(rows[-1]["t"]) if more and rows else None,
        }
    rows = queries.readings_bucketed(conn, device_id, bucket, t_from, t_to, limit)
    return {
        "device_id": device_id,
        "bucket": bucket,
        "readings": [_reading_out(r) for r in rows],
        "next_cursor": None,
    }


@router.get(
    "/devices/{device_id}/events",
    response_model=EventsOut,
    dependencies=READ,
    responses={**AUTH_RESPONSES, **NOT_FOUND},
)
def get_events(
    device_id: str,
    conn: Conn,
    kind: Literal["ota", "dl_ack", "status"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> Any:
    _require_device(conn, device_id)
    kinds = (kind,) if kind else queries.EVENT_KINDS
    rows = queries.list_events(conn, device_id, kinds, limit)
    return {"events": [_event_out(r) for r in rows]}


@router.get(
    "/devices/{device_id}/sensors",
    response_model=SensorsOut,
    dependencies=READ,
    responses={**AUTH_RESPONSES, **NOT_FOUND},
)
def get_sensors(device_id: str, conn: Conn) -> Any:
    _require_device(conn, device_id)
    return {"sensors": [_sensor_out(r) for r in queries.list_sensors(conn, device_id)]}


_DUPLICATE = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="A sensor with this valid_from already exists for the device",
)


@router.post(
    "/devices/{device_id}/sensors",
    response_model=SensorOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=WRITE,
    responses={
        **WRITE_RESPONSES,
        **NOT_FOUND,
        409: {"model": Detail, "description": "valid_from already exists"},
    },
)
def create_sensor(device_id: str, body: SensorIn, conn: Conn) -> Any:
    _require_device(conn, device_id)
    try:
        row = queries.insert_sensor(conn, device_id, body.row())
    except queries.DuplicateValidFrom:
        raise _DUPLICATE from None
    return _sensor_out(row)


@router.put(
    "/devices/{device_id}/sensors/{sensor_id}",
    response_model=SensorOut,
    dependencies=WRITE,
    responses={
        **WRITE_RESPONSES,
        404: {"model": Detail, "description": "Unknown device or sensor"},
        409: {"model": Detail, "description": "valid_from already exists"},
    },
)
def replace_sensor(device_id: str, sensor_id: int, body: SensorIn, conn: Conn) -> Any:
    existing = queries.get_sensor(conn, device_id, sensor_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown sensor")
    try:
        row = queries.update_sensor(
            conn,
            device_id,
            sensor_id,
            body.row(default_valid_from=existing["valid_from"]),
        )
    except queries.DuplicateValidFrom:
        raise _DUPLICATE from None
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown sensor")
    return _sensor_out(row)


@router.delete(
    "/devices/{device_id}/sensors/{sensor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=WRITE,
    responses={
        **WRITE_RESPONSES,
        404: {"model": Detail, "description": "Unknown device or sensor"},
    },
)
def delete_sensor(device_id: str, sensor_id: int, conn: Conn) -> Response:
    if not queries.delete_sensor(conn, device_id, sensor_id):
        raise HTTPException(status_code=404, detail="Unknown sensor")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
