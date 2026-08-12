from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app


def now() -> datetime:
    provider = current_app.config.get("NOW_PROVIDER")
    if provider:
        value = provider()
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(current_app.config["TIMEZONE"]))
        return value
    return datetime.now(ZoneInfo(current_app.config["TIMEZONE"]))


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(current_app.config["TIMEZONE"]))
    return parsed
