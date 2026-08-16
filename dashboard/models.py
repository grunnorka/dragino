"""Lightweight typed views for dashboard rows (optional helpers)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class DeviceRow:
    id: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    created_via: str
    status: str
    stale: bool
    battery: float | None = None
    signal: float | None = None
    model: str | None = None
    imei: str | None = None

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> DeviceRow:
        return cls(
            id=row["id"],
            first_seen_at=row.get("first_seen_at"),
            last_seen_at=row.get("last_seen_at"),
            created_via=row["created_via"],
            status=row.get("status", "never-seen"),
            stale=bool(row.get("stale", True)),
            battery=row.get("battery"),
            signal=row.get("signal"),
            model=row.get("model"),
            imei=row.get("imei"),
        )
